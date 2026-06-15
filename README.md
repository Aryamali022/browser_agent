# Browser Agent

An AI browser agent that controls your browser to complete tasks for you. Tell it
what to do in plain language ("search for X and open the first result", "fill this
form", "summarize this page") and it runs an **observe → plan → execute** loop using
an LLM, acting on the page through a Chrome extension side panel.

- **Backend** — Python / FastAPI agent loop, a 25-tool registry, a security guard,
  SQLite memory, and an LLM abstraction over NVIDIA NIM / OpenAI / Gemini. Vision
  fallback uses a VLM to locate elements by screenshot when DOM targeting fails.
- **Extension** — a React 19 + TypeScript + Vite + Tailwind v4 Chrome extension
  (Manifest V3) that renders the side-panel UI and executes actions on the page.

The two halves talk over a WebSocket: the extension connects to
`ws://localhost:8000/ws`, so **the backend must be running for the extension to work.**

---

## Prerequisites

- **Python 3.11**
- **Node.js 18+** and npm
- **Google Chrome** (or any Chromium browser that supports the Side Panel API)
- An **NVIDIA NIM API key** (default LLM provider) — or an OpenAI / Gemini key

---

## Setup

### 1. Configure environment variables

Copy the example file and fill in your real values:

```bash
cd backend
cp .env.example .env      # Windows: copy .env.example .env
```

Edit `backend/.env` and set at least your `NVIDIA_API_KEY` (and `DATABASE_URL` if
you use one). **This file is git-ignored — never commit it.**

### 2. Start the backend

```bash
cd backend
python -m venv venv
venv\Scripts\activate          # macOS/Linux: source venv/bin/activate
pip install -r requirements.txt
python -m uvicorn main:app --port 8000
```

Leave this running. The API is now at `http://localhost:8000` and the WebSocket at
`ws://localhost:8000/ws`.

### 3. Build the extension

```bash
cd extension
npm install
npm run build
```

This produces a `extension/dist/` folder (the loadable extension).

### 4. Load the extension in Chrome

1. Open `chrome://extensions`.
2. Toggle **Developer mode** on (top-right).
3. Click **Load unpacked** and select the **`extension/dist`** folder.
4. The **Browser Agent** extension appears in your list.

---

## How to use it

1. Make sure the **backend is running** (step 2 above).
2. Click the **Browser Agent** icon in the Chrome toolbar (or pin it first via the
   puzzle-piece menu). This opens the **side panel**.
3. Wait for the connection indicator to show it's connected to the backend.
4. Type a task in plain language and send it, for example:
   - `Search Google for "best laptops 2026" and open the first result`
   - `Fill in the login form with my email and a placeholder password`
   - `Summarize the main points of this article`
5. Watch the side panel stream each step as the agent **observes** the page,
   **plans** an action, and **executes** it (clicking, typing, navigating, scrolling).
6. The agent stops when the task is complete, and shows the result in the panel.

> **Tip:** The agent acts on the currently active tab. Keep the tab you want it to
> work on in focus.

### What it can do

The agent has a registry of ~25 tools — navigate, click, type, scroll, read page
content, take screenshots, and more. When it can't find an element via the DOM, it
falls back to **vision**: it screenshots the page, asks a vision model to locate the
target, and clicks by coordinates.

### Permissions

The extension requests `activeTab`, `scripting`, `storage`, `tabs`, `sidePanel`, and
host access to all URLs (`<all_urls>`) so it can read and act on the pages you ask it
to work with.

---

## Development

```bash
# Extension dev build / lint
cd extension
npm run dev       # vite dev server
npm run lint
```

If you change the WebSocket message format, keep the backend and
`extension/src/types.ts` in sync.

---

## Project structure

```
backend/      FastAPI agent loop, tools, LLM abstraction, memory (run from here)
extension/    React + Vite Chrome extension (build to extension/dist)
vision/       Vision-fallback interface
voice/        Voice interface (stub)
```

---

## Troubleshooting

- **Side panel says "disconnected"** — the backend isn't running, or not on port
  8000. Start it and the extension will auto-reconnect.
- **LLM errors / empty responses** — check that `NVIDIA_API_KEY` (or your chosen
  provider key) is set correctly in `backend/.env`.
- **Extension changes don't appear** — re-run `npm run build`, then click the reload
  icon on the extension card in `chrome://extensions`.
