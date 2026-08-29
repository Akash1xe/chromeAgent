# Chrome Agent

A local web-based autonomous browser agent built with **Browser Use**, **Playwright Chromium**, **FastAPI**, **WebSockets**, and **React**.

The agent accepts a natural-language browser task, observes the current browser state through Browser Use, chooses the next action with the configured LLM provider, executes it in Chromium, and streams progress plus screenshots to the local dashboard.

## Architecture

```text
React / Vite UI (localhost:5173)
        |
        | REST + WebSocket
        v
FastAPI backend (localhost:8000)
        |
        +-- BrowserAgent
        |     +-- Playwright launches Chromium
        |     +-- Browser Use attaches through CDP
        |     +-- Browser Use agent loop + DOM extraction
        |     +-- live screenshots + intervention controls
        |
        +-- LLMRouter
              1. Groq key rotation (up to 5 keys)
              2. Gemini fallback
              3. Local Ollama fallback
```

## Included features

- Natural-language task input.
- Provider selection: Auto fallback, Groq, Gemini, or Ollama.
- Headed/headless Chromium.
- Configurable max steps.
- Real-time WebSocket step logs.
- Per-step provider, reasoning, action, result, and success/failure.
- Live browser screenshot streaming in headless mode.
- Pause, Take Over, Resume, and Stop controls.
- Manual click/type/Enter/Tab/Escape from the live browser panel during takeover.
- CAPTCHA, login, OTP/2FA detection with automatic pause.
- Payment/checkout detection with an explicit **Confirm & Resume** control.
- Repeated anti-bot blocking detection and stop.
- Browser Use DOM-indexed browser actions.
- Browser Use JavaScript fallback for interactions such as hover/drag when needed.
- Tabs, forms, dropdowns, keyboard actions, uploads, page extraction, iframes, and dialogs through Browser Use/browser CDP capabilities.
- Vision fallback after two consecutive states without a usable DOM selector map.
- 30-second LLM/step timeout.
- Two retries after an initial failed Browser Use step (`max_failures=3`).
- Random 300-900 ms delay between completed agent steps.
- JSON task history under `backend/logs/`.
- Local provider usage counters.
- Add/remove/test API keys from Settings.
- Per-key tests for all five Groq keys and Gemini.
- Configurable provider priority.

## Provider defaults

Defaults are configurable in `.env`:

```env
GROQ_MODEL=llama-3.3-70b-versatile
GEMINI_MODEL=gemini-3.7-flash
OLLAMA_MODEL=mistral-small
PROVIDER_PRIORITY=groq,gemini,ollama
```

The Gemini model default was verified against Google's current model/free-tier information when this project was created. If a provider changes its model availability later, update the corresponding environment variable without changing application code.

## Requirements

- Windows 11, macOS, or Linux
- Python **3.11+**
- Node.js 20+ recommended
- npm
- Internet access for Groq/Gemini
- Ollama only if you want the local fallback

## Windows quick start

Clone and enter the repository:

```powershell
git clone https://github.com/Akash1xe/chromeAgent.git
cd chromeAgent
```

Run the first-time bootstrap:

```powershell
.\setup.ps1
```

If PowerShell blocks local scripts for the current shell, use:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\setup.ps1
```

The setup script creates `.venv`, installs backend dependencies, installs Playwright Chromium, installs frontend dependencies, creates `.env` when missing, and runs environment diagnostics.

After setup, add any Groq/Gemini keys you want to use to `.env`, then start the whole app with one command:

```powershell
.\.venv\Scripts\python.exe run.py
```

The launcher starts FastAPI and Vite together, opens `http://127.0.0.1:5173`, and stops both child processes when you press Ctrl+C.

You can rerun diagnostics at any time:

```powershell
cd backend
..\.venv\Scripts\python.exe doctor.py
```


### Local performance benchmark

Before testing LLM-driven tasks, you can measure raw Chromium startup and direct-navigation speed without consuming any Groq, Gemini, or Ollama quota:

```powershell
cd backend
..\.venv\Scripts\python.exe acceptance_benchmark.py --url https://www.flipkart.com --runs 3
```

The benchmark reports Chromium launch time, navigation time, and total median time. This isolates browser/runtime performance from model latency.

For the first dashboard acceptance run, use:

```text
Open Flipkart
```

That command should use the deterministic direct-navigation path and show provider `direct` in the step log. Then test a true agent task such as:

```text
Open Flipkart and search for laptops
```

The second command should use Browser Use and the configured LLM fallback chain.

Manual setup remains possible if preferred.

Create and activate a virtual environment:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
```

Install backend dependencies and Chromium:

```powershell
pip install -r backend\requirements.txt
playwright install chromium
```

Create your local environment file:

```powershell
Copy-Item .env.example .env
```

Edit `.env` and add the API keys you want to use:

```env
GROQ_API_KEY_1=
GROQ_API_KEY_2=
GROQ_API_KEY_3=
GROQ_API_KEY_4=
GROQ_API_KEY_5=
GEMINI_API_KEY=
```

Keys are intentionally ignored by Git. Never commit the real `.env` file.

### Optional Ollama fallback

Install Ollama, then:

```powershell
ollama pull mistral-small
ollama serve
```

The default Ollama endpoint is `http://127.0.0.1:11434`.

## Run the backend

Open a terminal:

```powershell
cd chromeAgent\backend
..\.venv\Scripts\Activate.ps1
uvicorn main:app --reload --host 127.0.0.1 --port 8000
```

Health check:

```text
http://127.0.0.1:8000/api/health
```

## Run the frontend

Open a second terminal:

```powershell
cd chromeAgent\frontend
npm install
npm run dev
```

Open:

```text
http://localhost:5173
```

## Using the agent

1. Type a browser task.
2. Choose the starting provider or **Auto fallback**.
3. Choose headed/headless mode and max steps.
4. Click **Run**.
5. Watch screenshots and step logs in real time.
6. If the agent encounters authentication, CAPTCHA, or OTP/2FA, click **Take Over** and interact with the screenshot panel.
7. Click **Resume** when finished.
8. For payment/checkout, the agent pauses and the resume control becomes **Confirm & Resume**.

## Groq fallback behavior

When Groq is active, the router starts with one configured key and rotates to another configured Groq key on an HTTP 429 rate-limit response. Calls are counted locally per key for the current day and rolling minute.

When no Groq key can answer, the router falls through according to `PROVIDER_PRIORITY`, which defaults to:

```text
Groq -> Gemini -> Ollama
```

## Vision fallback

Browser Use remains DOM-first. At the beginning of every step the backend checks the current Browser Use selector map.

If no usable selector map is available for two consecutive states:

- vision mode is enabled for the agent,
- Gemini is prioritized when configured,
- the current Browser Use screenshot becomes available to the multimodal provider.

This keeps ordinary browsing fast and DOM-native while still allowing canvas-like or otherwise inaccessible pages to use visual reasoning.

## File uploads

Use the file picker in the Task panel before starting a run. Selected files are copied into the restricted local `backend/uploads/` directory with sanitized generated names, and only the exact files attached to that run are exposed to Browser Use.

Uploads are limited to 20 MB per file and 10 files per run. Arbitrary local filesystem paths are not exposed to the agent. Runtime upload contents are ignored by Git.

## Logs and history

Each finished run is written to:

```text
backend/logs/<run_id>.json
```

The History page displays the task, status, duration, step count, and providers actually used. Opening a history item shows its saved step information.

## Settings

The Settings screen lets you:

- save or remove each Groq/Gemini key,
- test each individual key,
- test each provider,
- inspect locally tracked request counts,
- change the provider priority order.

The UI only receives masked versions of stored keys.

## Safety behavior

The agent intentionally pauses rather than trying to bypass CAPTCHA, login verification, or 2FA/OTP.

It also pauses before payment/checkout confirmation. The user must explicitly resume from the UI.

Repeated site-level anti-bot blocking causes the run to fail rather than retry indefinitely.

## Tests

With the backend environment installed:

```powershell
cd backend
pytest -q
```

The test suite covers API/config validation, provider fallback and Groq key rotation, run lifecycle persistence, manual CDP controls, Browser Use tool availability, real Chromium/CDP attachment, screenshots, browser primitives, secure uploads, navigation fast-path detection, and DOM-to-vision recovery.

## Main API

```text
POST   /api/runs
WS     /ws/runs/{run_id}

POST   /api/runs/{run_id}/pause
POST   /api/runs/{run_id}/takeover
POST   /api/runs/{run_id}/resume
POST   /api/runs/{run_id}/stop

POST   /api/runs/{run_id}/manual/click
POST   /api/runs/{run_id}/manual/type
POST   /api/runs/{run_id}/manual/key
POST   /api/runs/{run_id}/manual/scroll
POST   /api/uploads

GET    /api/history
GET    /api/history/{run_id}

GET    /api/settings
POST   /api/settings/key
DELETE /api/settings/key/{name}
POST   /api/settings/test-key/{name}
POST   /api/settings/test/{provider}
POST   /api/settings/priority
```

## Notes

- This project uses **local Browser Use**, not Browser Use Cloud.
- Playwright owns Chromium process startup; Browser Use attaches to that browser through CDP and owns the autonomous agent loop.
- Do not expose the FastAPI service publicly without authentication. The intervention endpoints can directly control the active browser.
