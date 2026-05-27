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
ANTHROPIC_API_KEY=...       # chat (Claude Sonnet claude-sonnet-4-6)
GOOGLE_MAPS_API_KEY=...     # geocoding
RAKUTEN_APP_ID=...          # Rakuten Travel API
RAKUTEN_ACCESS_KEY=...      # Rakuten Travel API
```

create a env.example in the project 
---

## Rokuten Travel API
Refer to API doc under /backend/api/api_doc

## Google Geolocating API

## Sub agents
1. frontend-developer 
2. backend-developer
3. ai-engineer
4. test-automation
5. 

## Version Roadmap

### V1 — Simple (current)
- 2 tools: geocoding + rakuten
- 1 agent, 1 FastAPI backend, 1 React frontend
- Direct function calls between tools
- LLM: OpenAI `text-embedding-3-small` (embeddings) + Claude Sonnet (chat)
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
