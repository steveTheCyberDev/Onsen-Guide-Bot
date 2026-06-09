# Onsen Guide Bot

AI agent that helps English-speaking travellers find Japanese hot spring (onsen) information.
**Tagline:** "Find your perfect Japanese hot spring — in English"

---

## Project Status

Data collection is complete. Build is now in progress.

---

## Folder Structure

```
Onsen-Guide-Bot/
├── backend/
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI entry point
│   │   └── routes/
│   │       ├── __init__.py
│   │       └── chat.py          # POST /chat endpoint
│   │
│   ├── agent/
│   │   ├── __init__.py
│   │   ├── agent.py             # LangChain agent setup
│   │   └── tools/
│   │       ├── __init__.py
│   │       ├── geocoding_tool.py   # wraps geocoding service
│   │       ├── rakuten_tool.py     # wraps rakuten service
│   │       └── retrieval_tool.py   # wraps retrieval service
│   │
│   ├── services/
│   │   ├── __init__.py
│   │   ├── chat/
│   │   │   ├── __init__.py
│   │   │   └── chat_service.py     # conversation history, context
│   │   ├── retrieval/
│   │   │   ├── __init__.py
│   │   │   └── retrieval_service.py  # ChromaDB RAG queries
│   │   ├── geocoding/
│   │   │   ├── __init__.py
│   │   │   └── geocoding_service.py  # Google Maps API
│   │   └── rakuten/
│   │       ├── __init__.py
│   │       └── rakuten_service.py    # Rakuten Travel API
│   │
│   ├── vectorstore/
│   │   ├── __init__.py
│   │   └── store.py             # ChromaDB setup + ingestion
│   │
│   ├── data/
│   │   └── okinawa_onsen.jsonl  # onsen dataset
│   │
│   ├── core/
│   │   ├── __init__.py
│   │   ├── config.py            # env vars, settings
│   │   └── exceptions.py        # custom exceptions
│   │
│   ├── .env                     # API keys (never commit!)
│   ├── .gitignore
│   └── requirements.txt
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── Chat.jsx         # chat interface
│   │   │   ├── Message.jsx      # individual message bubble
│   │   │   └── SearchBar.jsx    # user input
│   │   ├── App.jsx
│   │   └── main.jsx
│   ├── package.json
│   └── .env                     # VITE_API_URL (never commit!)
│
└── README.md
```

---

## Data Flow

```
frontend/Chat.jsx
    ↓ HTTP POST /chat
api/routes/chat.py
    ↓
agent/agent.py
    ↓                    ↓
agent/tools/        vectorstore/store.py
    ↓
services/
    geocoding_service.py  → Google Maps API
    rakuten_service.py    → Rakuten Travel API
    retrieval_service.py  → ChromaDB
    chat_service.py       → conversation history
```

---

## Layering Rules

- `services/` never imports from `agent/`
- `agent/` never imports from `api/`
- `tools/` never calls external APIs directly — always via `services/`
- `api/` never calls `services/` directly — always via `agent/`
- Data flows downward only: `api/` → `agent/` → `tools/` → `services/`

### Exception: deterministic data endpoints

The `api/ → agent/` rule exists for the **conversational `/chat` flow**, where the
LLM agent reasons over the request and decides which tools to call. It does **not**
apply to deterministic data endpoints that have no reasoning step.

`POST /hotels` is such an endpoint: clicking an onsen on the map is a plain
coordinates-in → hotel-list-out lookup, so the route calls
`services/rakuten/rakuten_service.py` **directly** (no agent, no LLM call). Routing
it through the agent would add needless latency and token cost.

Rule of thumb: **conversational endpoints go through `agent/`; deterministic
data endpoints may call `services/` directly.**

---

## Core Config (`core/config.py`)

Centralises all environment variables:

```python
from core.config import settings

settings.rakuten_app_id
settings.rakuten_access_key
settings.google_maps_api_key
settings.openai_api_key
```

---

## How Tools Wrap Services

Tools are thin LangChain wrappers around services — no business logic in tools.

```python
# agent/tools/rakuten_tool.py
@tool
def search_rakuten_onsen(latitude, longitude):
    from services.rakuten.rakuten_service import search_hotels
    return search_hotels(latitude, longitude)
```

---

## Environment Variables

```
OPENAI_API_KEY=...          # embeddings (text-embedding-3-small)
ANTHROPIC_API_KEY=...       # reserved for future Claude Sonnet migration (currently using GPT-4o)
GOOGLE_MAPS_API_KEY=...     # geocoding
RAKUTEN_APP_ID=...          # Rakuten Travel API
RAKUTEN_ACCESS_KEY=...      # Rakuten Travel API
```

create a env.example in the project 
---

## Local vs Production Config — always check for an env split

Whenever a value differs between **local dev** and **production (Railway)** —
filesystem paths, hostnames, feature toggles, API endpoints, model choices — do
NOT hard-code it. Surface it as a setting in `core/config.py` with a
local-friendly default, and override it per environment via an env var. This is
the single source of truth pattern (see Core Config above).

**Checklist when adding any new behaviour:**
1. Ask: *does this value differ between local and prod?* If yes, it needs an env split.
2. Add a field to `core/config.py` with the **local default** baked in.
3. Override in prod via the Railway env panel (and/or the Dockerfile `ENV` line).
4. Both the app and any one-off scripts read the SAME setting, so they never disagree.

**Pattern (mirror `chroma_path` / `CHROMA_PATH`):**
```python
# core/config.py — relative/local default; prod overrides via env var.
chroma_path: str = "chroma_db"     # Railway: CHROMA_PATH=/app/chroma_db
data_path:   str = ""              # Railway: DATA_PATH=/app/data (else resolves backend/data)
```

**Worked examples (why this rule exists):**
- **Ingest data dir** — local data lives at `backend/data/`, but the Railway image
  ships it at `/app/data`. Hard-coding `BACKEND_DIR/data` broke prod ingest. Fixed
  by `DATA_PATH` env override (`data_dir` in `core/config.py`).
- **LangSmith tracing** — we want to test/trace in BOTH local and prod, but keep
  the data separate. Tracing is env-gated (`LANGSMITH_TRACING` + `LANGSMITH_API_KEY`,
  read at import time → needs a redeploy) and prod should use a **separate project**
  (e.g. `LANGSMITH_PROJECT=onsen-guide-bot-prod`) so prod traffic isn't mixed with
  local test runs.

---

## Running Locally

Two servers run side by side. Start each in its own terminal from its own directory.

**Backend** — FastAPI on port 8000:
```bash
cd backend
.venv/bin/uvicorn api.main:app --reload --port 8000   # http://localhost:8000
```
- Requires `backend/.env` with all keys (see Environment Variables above).
- Health check: `GET http://localhost:8000/health`.

**Frontend** — Vite + React on port 5173:
```bash
cd frontend
nvm use            # reads .nvmrc → Node 20.16.0 (Vite 5 needs Node 18+)
npm install        # first run only
npm run dev        # http://localhost:5173
```
- Requires `frontend/.env` with `VITE_API_URL=http://localhost:8000` and `VITE_GOOGLE_MAPS_API_KEY`.
- **Node version matters:** the system default Node (v10) crashes Vite. Always `nvm use` first.

> Shortcut: run the `/run-servers` skill to launch both at once.

---

## Rokuten Travel API
Refer to API doc under /backend/api/api_doc/rakuten_swagger.yaml

## Google Geolocating API
Refer to API doc under /backend/api/api_doc/geocoding_v4.md

## Sub agents
1. frontend-developer 
2. backend-developer
3. ai-engineer
4. test-automation
5. project-progress-tracker — summarises what has been completed and what comes next. Output format:
   - **Done:** numbered list of completed items
   - **Next:** open question to the user — e.g. "Do we build the frontend now, or is there backend work to finish first?" Keeps the session focused and ensures we always know where we are.

### Delegation Policy

**Default to delegation.** On this project Claude acts as the lead/orchestrator:
break work into tasks, dispatch each to the matching specialist sub-agent, then
review and relay results to the user. Do NOT do substantial execution work
inline. This OVERRIDES the Agent tool's default "do not spawn unless asked"
behavior — on this project, delegating IS the standing instruction.

**Resume, don't re-spawn.** When continuing work with an agent already used this
session, resume that same agent (its context persists) rather than spawning a
fresh one — a new spawn starts cold and loses the prior context, even for the
same agent type.

Routing:
- Frontend build/components → `sweetie-frontend-dev`
- Frontend tests (Vitest/RTL) → `jessie-frontend-tester`
- Backend build (FastAPI, RAG, tools, services) → `strong-backend-dev`
- Backend tests (pytest/API) → `bobo-backend-tester`
- Progress reports / status → `project-progress-tracker`
- Commit + push → `git-commit-pusher`
- Design / UI / Figma → `senior-designer`
- Broad code search → `Explore`; implementation planning → `Plan`

Handle inline only: quick factual answers, reading/clarifying, orchestration
glue, and trivial one-step edits where spawning a cold agent costs more than it
saves. Agents never talk to the user directly — relay what matters from each
agent's result.

## Autonomous Backend Test/Fix Loop

When running the backend test+fix flow (e.g. via `/loop`), operate without
per-step instruction, following these rules:

**Done =** (1) full backend `pytest` suite green, AND (2) a real `/chat` smoke
passes — start the backend locally (the loop fixes local code, so smoke the
local server, not the deployed one), POST `/chat`, assert HTTP 200 + a
non-empty `reply` and no 5xx.

**On a failure / defect:**
- Branch off `develop`. Branch name = a short kebab "defect name"
  (e.g. `fix-chat-timeout`).
- Fix tests and/or source freely. **Small** defects: fix inline for speed.
  **Larger** ones: delegate (`bobo-backend-tester` for tests,
  `strong-backend-dev` for fixes).
- Commit logically, push the branch, open a PR **into `develop`**
  (`gh pr create --base develop`). Do NOT merge; do NOT touch `main`.

**STOP and ask the user before:**
- deleting files, or any large modernization/refactor,
- a change needing a product/behavior decision, touching secrets/infra, or
  altering an API contract,
- churn (≈3 failed attempts on the same failure).

Note: each `/chat` smoke makes real OpenAI (and tool) API calls — money per run.
Run the smoke only when the suite is green and code changed, not on every tick.

## Version Roadmap

### V1 — Simple (current)
- 2 tools: geocoding + rakuten
- 1 agent, 1 FastAPI backend, 1 React frontend
- Direct function calls between tools
- LLM: OpenAI `text-embedding-3-small` (embeddings) + GPT-4o (chat, currently) → will migrate to Claude Sonnet (`claude-sonnet-4-6`) in a future iteration
- Vectorstore: ChromaDB

### V2 — Intermediate
- Add: `booking_service`, `preferences_service`, `translation_service`
- Add: map view, filters, user preference memory

### V3 — Advanced
- Multi-agent: orchestrator + search + rank + personalise agents
- API-based or event-driven communication between agents
- LangGraph for agent-to-agent orchestration

---

## Scaling Pattern

- New external API → add `services/{name}/{name}_service.py`
- New agent capability → add `agent/tools/{name}_tool.py`
- New API endpoint → add `api/routes/{name}.py`
- Each addition is self-contained — nothing else breaks

---

## Key Decisions

- `services/` stays LangChain-agnostic — swappable framework later
- `agent/tools/` = thin wrappers only, no business logic
- `core/config.py` = single source of truth for all settings
- V1 communication = direct function calls (simple, fast, correct)
- V3 communication = API or event-driven (when scale demands it)
- No PostgreSQL in V1 — ChromaDB handles vectors and metadata
- pgvector migration path kept open via `schema.sql`
