# Desktop Automation Agent

A Windows desktop automation agent built with Python and Streamlit that can open desktop apps, generate content, fill documents/spreadsheets, and save files using AI-powered planning and vision-based execution.

## Overview

This project demonstrates an agent that interacts with Windows desktop applications by combining:

- AI task planning
- Computer vision-based action selection
- Windows keyboard and mouse automation
- App-specific workflows for Notepad, Word, Excel, and Outlook
- Recovery logic for unexpected dialogs and failures

It is designed as a proof-of-concept for automating repetitive desktop tasks using natural language instructions.

## Features

- Natural-language task execution from a Streamlit UI
- Automatic app detection and targeted launch flow
- Content generation for text documents and emails
- Excel data filling from structured AI-generated tables
- Save-as workflow handling for multiple file types
- Error recovery using screenshot diagnostics
- Optional Groq and Gemini AI backends for planning and vision inference

## Supported Use Cases

- Open an application from a user request
- Create and populate a text document
- Draft and send an email in Outlook
- Fill spreadsheet data in Excel
- Save output to a filename inferred from the task
- Recover from visual popups or app-state issues

## Tech Stack

- Python
- Streamlit
- PyAutoGUI
- Pyperclip
- Requests
- Groq API
- Gemini API (optional vision fallback)

## Project Structure

```text
Desktop_Automation_Agent/
├── README.md
├── agent.py
└── requirements.txt (if added in your environment)
```

## Requirements

Before running the project, install the Python dependencies:

```bash
pip install streamlit pyautogui pyperclip requests
```

For best results, configure your preferred AI service keys in `agent.py`:

- `GROQ_API_KEY`
- `GEMINI_API_KEY`

## Run the App

```bash
streamlit run agent.py
```

Then:

1. Enter a task in the app
2. Adjust the countdown and step limits if needed
3. Click Run Agent
4. Switch to your desktop when prompted
5. Watch the agent automate the workflow

## Example Tasks

- Open Notepad and write a project summary
- Open Excel and create a sales tracker table
- Open Word and draft a business proposal
- Open Outlook and compose a follow-up email
- Save the result with a generated filename

## Notes

This project is intended for Windows desktop environments and uses system-level automation. It is best suited for controlled, testable scenarios where the target applications are available and visible on screen.

## Disclaimer

This is a prototype / proof-of-concept project. While it is useful for exploring AI-assisted desktop automation, it should be used responsibly and carefully in environments where user approval and system safety are important.

## License

This project is provided as-is for educational and experimental use.

## Future Improvements

- More app-specific workflows
- Better OCR and screen understanding
- Safer validation before critical actions
- Multi-step task planning with less dependency on brittle UI assumptions
- Support for more document editors and productivity tools

---

Built to explore the intersection of AI, desktop automation, and practical Windows productivity workflows.
