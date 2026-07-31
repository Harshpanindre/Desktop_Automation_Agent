import streamlit as st
import pyautogui
import time
import json
import re
import io
import base64
import requests
import pyperclip
import threading
import queue

# -----------------------------------------------------------------------------
# GROQ  - Planner + Content Generator (text only, cheap)
GROQ_API_KEY = ""
GROQ_MODEL   = "llama-3.3-70b-versatile"
GROQ_VISION_MODEL = "llama-3.2-11b-vision-preview"
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"

# GEMINI - Vision fallback
GEMINI_API_KEY = ""
GEMINI_MODEL   = "gemini-2.0-flash"
GEMINI_URL     = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
# -----------------------------------------------------------------------------

class StopRequestedException(Exception):
    pass
APP_CONFIGS = {
    "code": {
        "search":    "code",
        "new_file":  ["ctrl+n"],
        "save_as":   ["ctrl+shift+s"],
        "pre_write": [],
        "clear":     True,
    },
    "notepad": {
        "search":    "notepad",
        "new_file":  [],
        "save_as":   ["ctrl+s"],
        "pre_write": [],
        "clear":     False,
    },
    "winword": {
        "search":    "winword",
        # Escape forces Word out of the template screen, Ctrl+N ensures a fresh doc
        "new_file":  ["escape", "ctrl+n"], 
        "save_as":   ["f12"], 
        "pre_write": [],
        "clear":     True,
    },
    "excel": {
        "search":    "excel",
        # Escape dismisses the template chooser, Ctrl+N guarantees a fresh workbook
        "new_file":  ["escape", "ctrl+n"], 
        "save_as":   ["f12"],
        "pre_write": [], # We removed the rogue "enter" that was causing issues
        "clear":     False,
    },
    "outlook": {
        "search":    "outlook",
        "new_file":  ["ctrl+n"],
        "save_as":   ["ctrl+s"],
        "pre_write": [],
        "clear":     False,
    },
}

EXT_SAVE_KEYS = {
    ".py":   ["ctrl+shift+s"],
    ".txt":  ["ctrl+s"],
    ".docx": ["f12"],
    ".xlsx": ["f12"],
    "default": ["ctrl+shift+s"],
}

PLANNER_PROMPT = """You are a task planner for a Windows desktop automation agent.
Given a user task, break it into an ordered list of stages.

Each stage must have:
- "name": one of these exact values: OPEN_APP, WRITE_CONTENT, SAVE_FILE, OUTLOOK_COMPOSE, EXCEL_FILL, ERROR_RECOVERY
- "goal": one clear sentence
- "max_steps": integer
- "done_hint": what confirms this stage is complete

Rules:
- Always start with OPEN_APP
- For Outlook email tasks use OUTLOOK_COMPOSE instead of WRITE_CONTENT
- For Excel spreadsheet tasks use EXCEL_FILL instead of WRITE_CONTENT
- For all other writing use WRITE_CONTENT
- Always end with SAVE_FILE
- Return ONLY a JSON array, no markdown"""

EXECUTOR_PROMPT = """You are a computer use agent controlling a Windows desktop via screenshots.
Complete ONE specific goal. Respond ONLY in valid JSON.

Available actions:
{"action": "click", "x": 450, "y": 300, "reason": "..."}
{"action": "double_click", "x": 450, "y": 300, "reason": "..."}
{"action": "type", "text": "short text", "reason": "..."}
{"action": "type_block", "text": "multi\\nline\\ntext", "reason": "..."}
{"action": "key", "key": "ctrl+n", "reason": "..."}
{"action": "key", "key": "escape", "reason": "close unexpected dialog"}
{"action": "wait", "seconds": 2, "reason": "..."}
{"action": "done", "reason": "goal confirmed complete"}

Rules:
- Use type_block for ALL multi-line content
- Use type only for short single-line text
- ONE action per response
- NEVER click x<15 or y<15
- If you see an unexpected dialog, error popup, or warning - press Escape first
- Do not repeat completed actions
- If Save As dialog is open: type filename then Enter"""

ERROR_RECOVERY_PROMPT = """You are diagnosing a Windows desktop automation failure.
Look at the screenshot and the failed goal. Determine what went wrong and what single action will recover.

Respond ONLY in valid JSON:
{"action": "key", "key": "escape", "reason": "dismiss unexpected dialog"}
OR
{"action": "click", "x": 450, "y": 300, "reason": "click OK on error dialog"}
OR
{"action": "wait", "seconds": 2, "reason": "wait for app to finish loading"}
OR
{"action": "recovered", "reason": "screen looks normal, ready to retry"}"""


def _groq_text(messages: list, max_tokens: int = 800, temp: float = 0.1) -> str:
    payload = {"model": GROQ_MODEL, "max_tokens": max_tokens, "temperature": temp, "messages": messages}
    for attempt in range(3):
        resp = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}, json=payload, timeout=30)
        if resp.status_code == 429:
            time.sleep(min(int(resp.headers.get("retry-after", 10)) + 2, 30))
            continue
        if resp.status_code != 200:
            raise Exception(f"Groq {resp.status_code}: {resp.text}")
        return resp.json()["choices"][0]["message"]["content"].strip()
    raise Exception("Groq: max retries exceeded")

def take_screenshot() -> str:
    shot = pyautogui.screenshot()
    buf  = io.BytesIO()
    shot.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("utf-8")

def _build_prompt_text(goal, done_hint, history, step):
    history_text = ""
    if history:
        history_text = "\n\nCompleted actions:\n" + "\n".join(f"  {i+1}. {a}" for i, a in enumerate(history[-10:]))
    return f"Current goal: {goal}\nDone when: {done_hint}\nStep: {step}{history_text}\n\nWhat is the single next action? Raw JSON only."

def ask_gemini_vision(screenshot_b64: str, system_prompt: str, user_text: str) -> str:
    payload = {
        "system_instruction": {"parts": [{"text": system_prompt}]},
        "contents": [{"parts": [{"inline_data": {"mime_type": "image/png", "data": screenshot_b64}}, {"text": user_text}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 300},
    }
    try:
        resp = requests.post(GEMINI_URL, headers={"Content-Type": "application/json"}, params={"key": GEMINI_API_KEY}, json=payload, timeout=30)
    except Exception: return "__UNAVAILABLE__"
    if resp.status_code == 429: return f"__RATELIMIT__{min(int(resp.headers.get('retry-after', 8)) + 1, 15)}"
    if resp.status_code != 200: return "__UNAVAILABLE__"
    try: return resp.json()["candidates"][0]["content"]["parts"][0]["text"].strip()
    except Exception: return "__UNAVAILABLE__"

def ask_executor(screenshot_b64: str, goal: str, done_hint: str, history: list, step: int, log_fn=None) -> str:
    def _log(m):
        if log_fn: log_fn(m)
    prompt_text = _build_prompt_text(goal, done_hint, history, step)
    
    if GEMINI_API_KEY and GEMINI_API_KEY != "your_gemini_api_key_here":
        raw = ask_gemini_vision(screenshot_b64, EXECUTOR_PROMPT, prompt_text)
        if raw not in ["__UNAVAILABLE__", "__BADKEY__"] and not raw.startswith("__RATELIMIT__"):
            return raw
            
    _log("[INFO] Using Groq vision fallback...")
    payload = {
        "model": GROQ_VISION_MODEL, "max_tokens": 300, "temperature": 0.1,
        "messages": [
            {"role": "system", "content": EXECUTOR_PROMPT},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}}, {"type": "text", "text": prompt_text}]},
        ],
    }
    for attempt in range(2):
        resp = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}, json=payload, timeout=30)
        if resp.status_code == 429:
            time.sleep(min(int(resp.headers.get("retry-after", 10)) + 2, 30))
            continue
        if resp.status_code == 200:
            return resp.json()["choices"][0]["message"]["content"].strip()
    return '{"action":"done","reason":"max retries hit"}'

def parse_action(text: str) -> dict:
    cleaned = re.sub(r"```json|```", "", text).strip()
    match   = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if match: cleaned = match.group(0)
    return json.loads(cleaned)

def execute_action(action: dict) -> tuple[str, bool]:
    act    = action["action"]
    reason = action.get("reason", "")
    if act in ("click", "double_click") and (action.get("x", 0) < 15 or action.get("y", 0) < 15):
        return f"[WARNING] Blocked unsafe click ({action.get('x')},{action.get('y')})", False

    if act == "click":
        pyautogui.click(action["x"], action["y"])
        return f"[ACTION] Click ({action['x']},{action['y']})", False
    elif act == "double_click":
        pyautogui.doubleClick(action["x"], action["y"])
        return f"[ACTION] DblClick ({action['x']},{action['y']})", False
    elif act == "type":
        pyautogui.write(action["text"], interval=0.05)
        return f"[ACTION] Typed short text", False
    elif act == "type_block":
        pyperclip.copy(action["text"])
        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "v")
        return f"[ACTION] Pasted text block", False
    elif act == "key":
        keys = action["key"].split("+")
        pyautogui.hotkey(*keys) if len(keys) > 1 else pyautogui.press(keys[0])
        return f"[ACTION] Pressed key: {action['key']}", False
    elif act == "wait":
        time.sleep(action.get("seconds", 2))
        return f"[ACTION] Waited {action.get('seconds', 2)}s", False
    elif act in ("done", "recovered"):
        return f"[SUCCESS] {reason}", True
    return f"[WARNING] Unknown action: {act}", False

def _groq_ask_simple(system: str, user: str, max_tokens: int = 40) -> str:
    return _groq_text([{"role": "system", "content": system}, {"role": "user", "content": user}], max_tokens=max_tokens, temp=0)

def extract_app_name(task: str) -> str:
    raw = _groq_ask_simple("Reply with ONLY the Windows search term for the app (e.g. notepad, code, winword, excel, outlook).", task, max_tokens=10).lower().strip()
    clean = re.sub(r"[^a-z0-9]", "", raw)
    return clean if clean else "notepad"

def extract_filename_from_task(task: str) -> str:
    raw = _groq_ask_simple("Extract the filename from this task. Reply with ONLY the filename with extension.", task, max_tokens=20).strip()
    return raw.split()[-1] if "." in raw and len(raw) < 50 else "output.txt"

def extract_outlook_fields(task: str) -> dict:
    raw = _groq_text([
        {"role": "system", "content": "Extract email fields. Return ONLY valid JSON with keys: to, subject, body."},
        {"role": "user", "content": task},
    ], max_tokens=300, temp=0.2)
    match = re.search(r"\{.*\}", re.sub(r"```json|```", "", raw).strip(), re.DOTALL)
    if match:
        try: return json.loads(match.group(0))
        except Exception: pass
    return {"to": "manager@example.com", "subject": "Update", "body": task}

def generate_content_for_task(task: str, goal: str, log_fn) -> str:
    log_fn("[INFO] Generating content...")
    raw = _groq_text([
        {"role": "system", "content": "Generate raw content to paste. No markdown fences."},
        {"role": "user", "content": f"Task: {task}\nGoal: {goal}\n\nContent:"},
    ], max_tokens=600, temp=0.2)
    return re.sub(r"^```[\w]*\n?", "", raw).rstrip("`").strip()

def generate_excel_data(task: str, log_fn) -> list[list[str]]:
    log_fn("[INFO] Generating Excel data array...")
    raw = _groq_text([
        {"role": "system", "content": "Generate tabular data for Excel. Return ONLY a JSON array of arrays."},
        {"role": "user", "content": f"Task: {task}\n\nArray:"},
    ], max_tokens=500, temp=0.2)
    match = re.search(r"\[.*\]", re.sub(r"```json|```", "", raw).strip(), re.DOTALL)
    if match:
        try: return json.loads(match.group(0))
        except Exception: pass
    return [["Item", "Value"], ["Row 1", "100"], ["Row 2", "200"]]

def stage_open_app(task: str, log_fn):
    app_key = extract_app_name(task)
    cfg = APP_CONFIGS.get(app_key, APP_CONFIGS["notepad"])
    
    log_fn(f"[ACTION] Launching {cfg['search']} via Windows Search")
    pyautogui.press("win")
    time.sleep(1.2)
    pyautogui.write(cfg["search"], interval=0.08)
    time.sleep(1.5)
    pyautogui.press("enter")
    time.sleep(3.5) 

    for key_combo in cfg["new_file"]:
        k = key_combo.split("+")
        pyautogui.hotkey(*k) if len(k) > 1 else pyautogui.press(k[0])
        time.sleep(1.2)

    for key_combo in cfg["pre_write"]:
        k = key_combo.split("+")
        pyautogui.hotkey(*k) if len(k) > 1 else pyautogui.press(k[0])
        time.sleep(0.8)

    if cfg.get("clear", False):
        time.sleep(0.5)
        pyautogui.hotkey("ctrl", "a")
        pyautogui.press("delete")
        
    return app_key

def stage_write_content(task: str, goal: str, log_fn):
    content = generate_content_for_task(task, goal, log_fn)
    if content:
        pyperclip.copy(content)
        time.sleep(0.3)
        pyautogui.hotkey("ctrl", "v")
        time.sleep(0.5)

def stage_outlook_compose(task: str, log_fn):
    fields = extract_outlook_fields(task)
    log_fn("[INFO] Populating Outlook fields")
    time.sleep(0.5)
    
    # 1. Type Email
    pyautogui.write(fields.get("to", "recipient@example.com"), interval=0.05)
    time.sleep(0.5)
    # Fix: Press Enter to dismiss autocomplete before tabbing
    pyautogui.press("enter") 
    time.sleep(0.2)
    
    # 2. Tab to Subject (To -> Cc -> Subject)
    pyautogui.press("tab") 
    time.sleep(0.2)
    pyautogui.press("tab")
    time.sleep(0.2)
    
    # 3. Type Subject
    pyautogui.write(fields.get("subject", "Update"), interval=0.05)
    time.sleep(0.3)
    
    # 4. Tab to Body
    pyautogui.press("tab") 
    time.sleep(0.3)
    
    # 5. Paste Body
    body = fields.get("body", "")
    if body:
        pyperclip.copy(body)
        time.sleep(0.2)
        pyautogui.hotkey("ctrl", "v")

def stage_excel_fill(task: str, log_fn):
    data = generate_excel_data(task, log_fn)
    pyautogui.hotkey("ctrl", "home")
    time.sleep(0.5)
    tsv_content = "\n".join("\t".join(str(cell) for cell in row) for row in data)
    pyperclip.copy(tsv_content)
    time.sleep(0.3)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.5)

def stage_save_file(task: str, app_key: str, log_fn):
    filename = extract_filename_from_task(task)
    ext = "." + filename.rsplit(".", 1)[-1] if "." in filename else "default"
    save_keys = APP_CONFIGS.get(app_key, {}).get("save_as", EXT_SAVE_KEYS.get(ext, ["ctrl+shift+s"]))

    log_fn("[ACTION] Initiating save sequence")
    for key_combo in save_keys:
        k = key_combo.split("+")
        pyautogui.hotkey(*k) if len(k) > 1 else pyautogui.press(k[0])
        time.sleep(2.0) 

    # Fix: Outlook doesn't need a filename dialog for drafts
    if app_key == "outlook":
        log_fn("[SUCCESS] Email saved to drafts.")
        return 

    pyautogui.hotkey("ctrl", "a")
    time.sleep(0.2)
    pyautogui.write(filename, interval=0.05)
    time.sleep(0.3)
    pyautogui.press("enter")
    time.sleep(1.5)

    if app_key == "excel" and not filename.endswith(".xlsx"):
        pyautogui.press("enter")
        time.sleep(0.5)

def stage_error_recovery(failed_goal: str, log_fn) -> bool:
    log_fn("[INFO] Running visual error diagnostic...")
    screenshot_b64 = take_screenshot()
    user_text = f"Goal: {failed_goal}\nFailed. What single action fixes this? Reply in JSON."

    if GEMINI_API_KEY and GEMINI_API_KEY != "your_gemini_api_key_here":
        raw = ask_gemini_vision(screenshot_b64, ERROR_RECOVERY_PROMPT, user_text)
    else:
        payload = {"model": GROQ_MODEL, "max_tokens": 150, "temperature": 0, "messages": [{"role": "system", "content": ERROR_RECOVERY_PROMPT}, {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_b64}"}}, {"type": "text", "text": user_text}]}]}
        resp = requests.post(GROQ_URL, headers={"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}, json=payload, timeout=30)
        raw = resp.json()["choices"][0]["message"]["content"].strip() if resp.status_code == 200 else '{"action":"key","key":"escape"}'

    try:
        action = parse_action(raw)
        msg, is_recovered = execute_action(action)
        log_fn(msg)
        time.sleep(1)
        return is_recovered or action.get("action") != "done"
    except Exception:
        log_fn("[WARNING] Diagnostic failed. Pressing Escape to clear popups.")
        pyautogui.press("escape")
        return True

def plan_task(task: str) -> list[dict]:
    raw = _groq_text([{"role": "system", "content": PLANNER_PROMPT}, {"role": "user", "content": f"Task: {task}\n\nJSON array only."}], max_tokens=800)
    match = re.search(r"\[.*\]", re.sub(r"```json|```", "", raw).strip(), re.DOTALL)
    return json.loads(match.group(0)) if match else json.loads(raw)

def run_agent_thread(task: str, log_q: queue.Queue, max_steps_per_stage: int, stop_event: threading.Event):
    pyautogui.FAILSAFE = True
    def log(msg): log_q.put(("log", msg))
    def done(msg): log_q.put(("done", msg))

    def check_stop():
        if stop_event.is_set():
            raise StopRequestedException("Stop requested")

    current_app_key = "notepad"
    log("--- GENERATING PLAN ---")
    try:
        check_stop()
        stages = plan_task(task)
    except StopRequestedException:
        done("[INFO] Agent execution stopped by user request.")
        return
    except Exception as e:
        done("[ERROR] Planner failed.")
        return

    for i, s in enumerate(stages):
        log(f"{i+1}. {s['name']} - {s['goal']}")
    time.sleep(1)

    for idx, stage in enumerate(stages):
        name, goal = stage["name"], stage["goal"]
        log(f"\n>>> STAGE {idx+1}: {name}")

        try:
            check_stop()
            if name == "OPEN_APP": current_app_key = stage_open_app(task, log)
            elif name in ("WRITE_CONTENT", "CREATE_FILE", "TYPE_CONTENT"): stage_write_content(task, goal, log)
            elif name == "OUTLOOK_COMPOSE": stage_outlook_compose(task, log)
            elif name == "EXCEL_FILL": stage_excel_fill(task, log)
            elif name == "SAVE_FILE": stage_save_file(task, current_app_key, log)
            elif name == "ERROR_RECOVERY": stage_error_recovery(goal, log)
            else:
                log("[INFO] Unknown stage, using vision executor loop...")
                history = []
                for step in range(1, stage.get("max_steps", max_steps_per_stage) + 1):
                    check_stop()
                    raw = ask_executor(take_screenshot(), goal, stage.get("done_hint", ""), history, step, log_fn=log)
                    action = parse_action(raw)
                    msg, is_done = execute_action(action)
                    log(msg)
                    history.append(msg)
                    if is_done: break
                    time.sleep(1.5)
            log(f"[SUCCESS] {name} completed")
        except StopRequestedException:
            done("[INFO] Agent execution stopped by user request.")
            return
        except Exception as e:
            log(f"[ERROR] Stage failed. Attempting recovery...")
            try:
                check_stop()
                stage_error_recovery(goal, log)
            except StopRequestedException:
                done("[INFO] Agent execution stopped by user request.")
                return

    done("--- TASK COMPLETE ---")


st.set_page_config(page_title="Philips IT PoC", layout="centered")

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;700&family=Syne:wght@400;700;800&display=swap');
html, body, [class*="css"] { font-family: 'Syne', sans-serif; }
.stApp { background: #0d0f14; color: #e8eaf0; }
h1 { font-family:'Syne',sans-serif!important; font-weight:800!important; font-size:2.4rem!important; color:#fff!important; letter-spacing:-1px; }
.tag { display:inline-block; background:#1a1f2e; border:1px solid #2a3045; color:#4a9eff; font-family:'JetBrains Mono',monospace; font-size:.7rem; padding:2px 10px; border-radius:20px; margin-right:6px; margin-bottom:20px; }
.stTextArea textarea { background:#131720!important; border:1px solid #2a3045!important; border-radius:10px!important; color:#e8eaf0!important; font-family:'JetBrains Mono',monospace!important; padding:14px!important; }
/* Style for secondary (Run) button */
div[data-testid="stButton"] button[data-testid="baseButton-secondary"] {
    background: linear-gradient(135deg,#4a9eff,#7b5ea7) !important;
    color: white !important;
    border: none !important;
    font-weight: 700 !important;
    padding: 12px 32px !important;
    width: 100% !important;
}
/* Style for primary (Stop) button */
div[data-testid="stButton"] button[data-testid="baseButton-primary"] {
    background: linear-gradient(135deg, #ff4b4b, #ff7676) !important;
    color: white !important;
    border: none !important;
    font-weight: 700 !important;
    padding: 12px 32px !important;
    width: 100% !important;
}
.log-box { background:#0a0c10; border:1px solid #1e2535; border-radius:10px; padding:18px 20px; font-family:'JetBrains Mono',monospace; font-size:.78rem; color:#7ecf8e; max-height:500px; overflow-y:auto; line-height:1.8; white-space:pre-wrap; }
.warn-box { background:#1a1500; border:1px solid #3d3000; border-radius:10px; padding:14px 18px; color:#f0c040; font-size:.85rem; margin-bottom:18px; font-family:'JetBrains Mono',monospace; }
</style>
""", unsafe_allow_html=True)

st.markdown("# Philips IT - Agent PoC")
st.markdown(
    '<span class="tag">Use Case 2</span>'
    '<span class="tag">Windows App Automation</span>'
    '<span class="tag">Error Recovery</span>',
    unsafe_allow_html=True,
)

st.markdown("""<div class="warn-box">Click <strong>Run Agent</strong>, then switch to your desktop during the countdown.</div>""", unsafe_allow_html=True)

task = st.text_area("Enter Task Request", placeholder="e.g. Open Excel, create a mutual fund tracking table, save as tracker.xlsx", height=100)

col1, col2 = st.columns(2)
with col1: countdown = st.slider("Countdown (seconds)", 3, 15, 7)
with col2: max_steps_per_stage = st.slider("Max steps per stage", 5, 20, 10)

# Initialize Session State
if "running" not in st.session_state:
    st.session_state["running"] = False
if "stop_event" not in st.session_state:
    st.session_state["stop_event"] = threading.Event()
if "log_lines" not in st.session_state:
    st.session_state["log_lines"] = []
if "log_q" not in st.session_state:
    st.session_state["log_q"] = queue.Queue()

# Run Button (disabled if running)
run_clicked = st.button("Run Agent", disabled=st.session_state["running"])

if run_clicked:
    if not task.strip():
        st.error("Please enter a task.")
    else:
        st.session_state["running"] = True
        st.session_state["stop_event"].clear()
        st.session_state["log_lines"] = []
        st.session_state["log_q"] = queue.Queue()

        cd = st.empty()
        for i in range(countdown, 0, -1):
            cd.markdown(f'<div class="warn-box">Starting in {i}s...</div>', unsafe_allow_html=True)
            time.sleep(1)
        cd.empty()

        # Start thread with arguments
        threading.Thread(
            target=run_agent_thread, 
            args=(task.strip(), st.session_state["log_q"], max_steps_per_stage, st.session_state["stop_event"]), 
            daemon=True
        ).start()
        st.rerun()

# Stop Button and Log Rendering
if st.session_state["running"]:
    if st.button("Stop Agent", type="primary", key="stop_btn"):
        st.session_state["stop_event"].set()
        st.session_state["running"] = False
        st.session_state["log_lines"].append("[INFO] Stop requested by user.")
        st.rerun()

    # Process logs
    log_q = st.session_state["log_q"]
    log_lines = st.session_state["log_lines"]
    has_new = False
    finished = False
    
    while not log_q.empty():
        kind, msg = log_q.get()
        log_lines.append(msg)
        has_new = True
        if kind == "done":
            finished = True

    if finished:
        st.session_state["running"] = False
        st.rerun()

    st.markdown('<div class="log-box">' + "\n".join(log_lines) + "</div>", unsafe_allow_html=True)
    
    # Rerun to poll queue
    if st.session_state["running"]:
        time.sleep(0.4)
        st.rerun()
else:
    # Display the final logs if they exist
    if st.session_state["log_lines"]:
        st.markdown('<div class="log-box">' + "\n".join(st.session_state["log_lines"]) + "</div>", unsafe_allow_html=True)