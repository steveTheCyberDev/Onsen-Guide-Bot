---
name: run-servers
description: Launch the Onsen Guide Bot backend (FastAPI) and frontend (Vite) dev servers together for local development.
---

# Run Local Dev Servers

Launches both servers for the Onsen Guide Bot. Run them in the background so the session stays interactive, then smoke-test each before reporting.

## Preflight

1. Confirm env files exist (do NOT print their contents):
   - `backend/.env` — fail early with a clear message if missing.
   - `frontend/.env` — must contain `VITE_API_URL` and a real `VITE_GOOGLE_MAPS_API_KEY` (not the `your_google_maps_api_key_here` placeholder).
2. Confirm `frontend/node_modules` exists; if not, run `npm install` first (see frontend step).

## Backend — FastAPI on :8000

Run from `backend/`, in the background:
```bash
cd backend && .venv/bin/uvicorn api.main:app --reload --port 8000
```
Smoke test once it's up:
```bash
curl -s http://localhost:8000/health   # expect {"status":"ok"}
```

## Frontend — Vite on :5173

The system default Node (v10) crashes Vite 5 — it needs Node 18+. The repo pins 20.16.0 via `frontend/.nvmrc`. Put that Node on PATH for the command:
```bash
cd frontend && export PATH="$HOME/.nvm/versions/node/v20.16.0/bin:$PATH" && npm run dev
```
- If `node_modules` is missing, run `npm install` (same PATH export) first.
- Server comes up at http://localhost:5173 — wait for the `Local:` line in the output.

## Report

- Print both URLs (frontend 5173, backend 8000) and the `/health` result.
- Note that both run in the background; to stop them: `pkill -f vite` and `pkill -f "uvicorn api.main"`.
- If the frontend shows a Google Maps "Oops!" error, the API key is missing or invalid in `frontend/.env`.
