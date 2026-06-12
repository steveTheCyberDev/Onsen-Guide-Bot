# Project Structure

> The folder/file map for Onsen Guide Bot. Referenced from `CLAUDE.md`.
> This is a **navigation map** (representative files + the rules that matter), not an
> exhaustive inventory — co-located tests (`*.test.jsx`), `__init__.py`, and `__pycache__`
> are omitted. Keep it in sync when directories move, or it sends readers to the wrong place.

```
Onsen-Guide-Bot/
├── backend/
│   ├── api/
│   │   ├── main.py              # FastAPI entry point
│   │   ├── api_doc/             # rakuten_swagger.yaml, geocoding_v4.md
│   │   └── routes/
│   │       ├── chat.py          # POST /chat (conversational → agent/workflow)
│   │       └── hotels.py        # POST /hotels (deterministic → rakuten_service directly)
│   │
│   ├── agent/
│   │   ├── agent.py             # LangGraph ReAct agent (legacy engine, kept for rollback)
│   │   ├── tools/               # thin wrappers: geocoding_tool, rakuten_tool, retrieval_tool
│   │   └── workflow/            # V2 deterministic workflow — the LIVE /chat engine
│   │       ├── pipeline.py      # orchestrates the 3 router modes
│   │       ├── intent.py        # parse_intent: route search/recommend/ask + extract slots
│   │       ├── analyze.py       # analyze_onsen: pros/cons + recommendation (judgment layer)
│   │       ├── ask.py           # ask-mode: semantic RAG over the knowledge base
│   │       ├── spring_benefits.py  # spring-type → benefit lookup table (not embeddings)
│   │       └── cost.py          # token/cost accounting
│   │
│   ├── services/                # framework-agnostic (no LangChain imports)
│   │   ├── chat/chat_service.py
│   │   ├── retrieval/retrieval_service.py   # ChromaDB RAG queries
│   │   ├── geocoding/geocoding_service.py   # Google Maps API
│   │   ├── rakuten/rakuten_service.py       # Rakuten Travel API
│   │   └── http_retry.py        # shared retry/timeout helper for external calls
│   │
│   ├── vectorstore/store.py     # ChromaDB setup (onsen + knowledge collections)
│   │
│   ├── scripts/                 # ops/CLI: ingest*, geocode_jsonl, eval_fabrication, eval_flow
│   │
│   ├── data/
│   │   ├── okinawa_springs.jsonl, tokai_springs.jsonl   # onsen dataset
│   │   └── knowledge/           # ask-mode KB: etiquette, tattoo_policy, spring_types, … (*.md)
│   │
│   ├── core/                    # config.py (settings, single source of truth), exceptions.py
│   ├── tests/                   # pytest suite
│   ├── .env                     # API keys (never commit!)
│   └── requirements.txt
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   │   ├── chat/            # ChatPanel, MessageList, OnsenMiniCard, ChatInput, …
│   │   │   ├── hotels/          # HotelPanel, HotelList, HotelCard, …
│   │   │   ├── map/             # MapPanel, OnsenMarker, HotelMarker, OnsenInfoStrip
│   │   │   ├── layout/          # MainLayout, Header, ResultsSummaryBar
│   │   │   └── shared/          # RakutenCredit
│   │   ├── reducer/appReducer.js  # useReducer state machine
│   │   ├── services/api.js      # centralised API helper (sends X-API-Key)
│   │   ├── config.js
│   │   ├── App.jsx, main.jsx
│   │   └── test/setup.js
│   ├── package.json
│   └── .env                     # VITE_API_URL, VITE_GOOGLE_MAPS_API_KEY (never commit!)
│
├── docs/                        # V2_IMPLEMENTATION_PLAN, ask-mode-plan, eval-model-comparison, …
├── PROJECT_JOURNEY.md           # full roadmap + engineering-challenge narrative
├── DEPLOYMENT.md
├── schema.sql                   # reserved pgvector migration path
└── README.md
```
