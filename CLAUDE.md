# Onsen Guide Bot

AI agent that helps English-speaking travellers find Japanese hot spring (onsen) information.
**Tagline:** "Find your perfect Japanese hot spring — in English"

---

## Project Status

V1 is **live in production** and feature-complete. V2's performance redesign (ingest-time
geocoding + ReAct→workflow, ~10× faster) and V2.5's 3-mode router + `analyze_onsen` guide
layer are **live**. Current build target: flip on the `ask`-mode knowledge base (built,
gated behind `ASK_ENABLED=False`). See Version Roadmap below and `PROJECT_JOURNEY.md`.

---

## Folder Structure

The full folder/file map lives in **`docs/PROJECT_STRUCTURE.md`** (kept separate so it can grow
without bloating this file). Read it for orientation instead of scanning the tree.

---

## Architecture

`POST /chat` runs the deterministic **workflow** engine (`agent/workflow/pipeline.py`). One small
LLM call (`parse_intent`) classifies the query into one of **3 modes** and branches:

```
                         POST /chat  (message)
                                 |
                      +----------+-----------+
                      |  ROUTER (parse_intent + mode)  |   1 small LLM call
                      +----------+-----------+
          +----------------------+-----------------------+
          v                      v                       v
       SEARCH                RECOMMEND                  ASK
   "onsen in Shizuoka"   "relaxing, mountains,    "what do I bring?
                          outdoor"                 do they allow tattoos?"
          |                      |                       |
          v                      v                       v
   query_onsen_structured   query_onsen_structured   semantic RAG over
   -> assemble (Python)     (candidates)             KNOWLEDGE docs
   -> template reply        -> ANALYTIC AGENT        -> grounded answer
   [built OK]                  (LLM: rank + pros/cons  [Layer 2]
                               grounded in prefs +
                               Layer-2 knowledge)
                             -> onsens + recommendation
                             [analyze_onsen]
```

- **search** — deterministic structured query + Python assembly (no LLM in the data path).
- **recommend** — retrieve candidates, then `analyze_onsen` (one analytical LLM call over a compact
  projection + prefs) returns a ranked recommendation + per-onsen `pros[]`/`cons[]`. Live via `ANALYZE_ENABLED=true`.
- **ask** — semantic RAG over the knowledge-base markdown docs (separate Chroma collection). Gated `ASK_ENABLED=False`.

Output: recommend/ask add `pros[]`/`cons[]` + a top-level `recommendation` string (additive); search leaves them empty.
The legacy ReAct agent (`agent/agent.py`) is retained behind `CHAT_ENGINE=react` for rollback only.

---

## Layering Rules

- `services/` never imports from `agent/`
- `agent/` never imports from `api/`
- `tools/` never calls external APIs directly — always via `services/`
- `api/` never calls `services/` directly — always via `agent/`
- Data flows downward only — two paths under `agent/`:
  - **Live workflow engine** (`CHAT_ENGINE=workflow`, default): `api/` → `agent/workflow/` → `services/` (no `tools/` hop).
  - **Legacy ReAct engine** (`CHAT_ENGINE=react`, rollback only): `api/` → `agent/` → `tools/` → `services/`.
  - `tools/` (thin LangChain wrappers) exist for the ReAct path only; the workflow calls `services/` directly.

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

## Current State (full roadmap + measured results in `PROJECT_JOURNEY.md`)

- Live `/chat` engine is the deterministic **workflow**, not ReAct (`CHAT_ENGINE=workflow`; ReAct retained for rollback).
- 3 router modes: `search` and `recommend` (live, `ANALYZE_ENABLED=true`) + `ask` (KB built, **gated** `ASK_ENABLED=False` — not yet flipped in prod).
- Agent / multi-agent (trip-planner), API-driven agent comms, and the GPT-4o→Claude Sonnet (`claude-sonnet-4-6`) migration are all **V3** — don't reach for them early.
- Guiding principle: the **autonomy ladder** (`rules → pipeline → workflow → agent → multi-agent`) — use the least autonomy that solves the task; climb a rung only when a concrete case can't be served below.

---

## Scaling Pattern

- New external API → add `services/{name}/{name}_service.py`
- New agent capability → add `agent/tools/{name}_tool.py`
- New API endpoint → add `api/routes/{name}.py`
- Each addition is self-contained — nothing else breaks

---

## Key Decisions

- `services/` stays LangChain-agnostic — no framework imports, so it's swappable.
- Storage is ChromaDB (vectors + metadata); pgvector migration path kept open via `schema.sql`.
