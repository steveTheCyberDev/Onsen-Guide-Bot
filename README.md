# Onsen Guide Bot

> **Find your perfect Japanese hot spring — in English.**
> A conversational AI agent that helps English-speaking travellers discover Japanese onsen (hot springs) and real nearby hotels, through chat + an interactive map.

**🔗 [Live Demo](https://onsen-guide-bot.vercel.app/)**

![React](https://img.shields.io/badge/React-18-61DAFB?logo=react&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![LangGraph](https://img.shields.io/badge/LangGraph-ReAct-1C3C3C)
![ChromaDB](https://img.shields.io/badge/ChromaDB-RAG-FF6F00)
![GPT--4o](https://img.shields.io/badge/GPT--4o-412991?logo=openai&logoColor=white)
![Railway](https://img.shields.io/badge/Railway-0B0D0E?logo=railway&logoColor=white)
![Vercel](https://img.shields.io/badge/Vercel-000000?logo=vercel&logoColor=white)

![Onsen Guide Bot demo](assets/gif/onsen-guide-compress.gif)

---

## What it does

English-language information about Japanese onsen is scarce and scattered. Onsen Guide Bot lets you simply ask — *"find me a sulfur onsen in Shizuoka"* — and get grounded, English-first answers backed by a real dataset, plotted on a map, with bookable hotels nearby.

- **Conversational, location-aware search** over a curated onsen dataset (RAG).
- **Interactive map** — results appear as markers; click a chat result to centre it.
- **Real hotels** — "see nearby hotels" returns live Rakuten Travel listings with prices, images, and booking links.
- **English-first**, with original Japanese names preserved.

> *Example:* "Any onsen near Naha with good water quality?" → a one-line summary, 3 onsen pinned on the map, and 10 real hotels within reach.

---

## Architecture

Strict, one-directional layering — each layer only knows about the one below it:

```
React (Vite + Tailwind)  ──HTTP──>  FastAPI
                                       │
                          LangGraph ReAct agent (GPT-4o)
                                       │   tools = thin wrappers
            ┌──────────────────────────┼──────────────────────────┐
      search_onsen             geocode_location           search_rakuten_onsen
            │                          │                            │
    retrieval_service          geocoding_service             rakuten_service
     (ChromaDB RAG)             (Google Maps)               (Rakuten Travel)
```

**The rules I enforced:**
- Data flows **downward only**: `api → agent → tools → services`.
- `services/` are **framework-agnostic** (zero LangChain imports) so the agent framework is swappable later.
- `tools/` are **thin wrappers** — no business logic.
- `core/config.py` is the **single source of truth** for settings.

> *I enforced strict layering so each component is independently testable and swappable.*

**One deliberate exception:** the `POST /hotels` endpoint (a map click → coordinates-in/hotels-out lookup) calls the service **directly**, skipping the agent. There's no reasoning step to justify the LLM latency and token cost. Conversational endpoints go through the agent; deterministic data endpoints don't.

**Retrieval design:** **one record = one embedding** — onsen are short, atomic entries, so I don't chunk them. A text splitter would only fragment a single onsen across vectors and add noise without benefit; I'd introduce one only for long-form content (full guides, reviews). Queries return the **top 20** matches (raised from a default 5 so "all onsen in X" returns a useful list), ranked semantically but constrained by a `prefecture_en` metadata filter (see Challenges #2).

---

## Technical decisions & tradeoffs

The judgement calls I'd defend in a design review.

### 1. LangGraph `create_react_agent` over a hand-rolled AgentExecutor
**Why:** It started simple — type an onsen name plus a location and see whether the agent could work out the coordinates itself. So I wrapped geocoding as a tool and let the agent decide when to call it: the classic reason → call tool → observe → repeat loop, without me owning the orchestration boilerplate. `create_react_agent` gave me that off the shelf, plus a clear migration path to a multi-agent graph in V3.
**Tradeoff:** Less control over each step than a bespoke loop, and a heavier dependency — a deliberate trade for V1 speed, with the graph abstraction already pointing at where V3 goes. (The agent's freedom to geocode every result at request time is also the main latency cost — addressed in #3 and the limitations below.)
**Where it evolves:** open-ended ReAct was right while query shapes were unknown, but it retrieves badly on under-specified queries (no location → weak RAG). V2 moves to a **slot-filling agent** on a hand-built LangGraph `StateGraph` — collect the required parameters (e.g. prefecture) *before* searching — for more predictable, cheaper, and accurate conversations. Design: [`docs/v2-slot-filling-agent.md`](./docs/v2-slot-filling-agent.md).

### 2. Structured output enforced at the schema layer (Pydantic `response_format`)
**Why:** The frontend doesn't want prose or a long summary — it wants structured data it can render however the product needs (cards, map markers, hotel lists). So the agent doesn't return free text; it's bound to a Pydantic `AgentResponse` (`reply`, `onsens[]`, `hotels[]`) via LangGraph's `response_format`. Every reply is a typed contract the UI can rely on, which keeps presentation decisions in the frontend where they belong instead of parsing them out of natural language.
**Bonus:** the schema is also where the anti-fabrication guarantees live (see Challenges #1) — the "must come verbatim from tool output, else empty list" rule sits in the field descriptions themselves, not just the prompt, so the contract enforces honesty as well as shape.

### 3. `async` + `asyncio.to_thread` for blocking calls
**Why:** The onsen dataset has no coordinates, so the agent geocodes each result via a blocking Google SDK call. Rather than serialise them, I run them **concurrently** with `asyncio.gather` over `asyncio.to_thread`, keeping the event loop free and collapsing N sequential geocodes into one parallel batch. The Rakuten lookup is offloaded the same way.
**Tradeoff:** This is mitigation, not a cure — the real fix is geocoding once at ingest (see Roadmap). But it buys a large latency win today for ~10 lines of code, and for V1 the remaining latency is acceptable.
**Where it evolves:** V2 splits the location work into two fixes. **Latency** — geocode each onsen *once at ingest* and store lat/lng in Chroma, dropping runtime geocoding entirely. **Accuracy** — instead of the agent guessing the user's intended location from free text, use **Google Places Autocomplete** in the frontend so the user picks an exact place (`place_id` + coordinates). That fills the location slot deterministically (see the [slot-filling design](./docs/v2-slot-filling-agent.md)) rather than relying on NLP extraction.

### 4. ChromaDB now, pgvector path kept open
**Why:** ChromaDB let me get the *whole flow* working and turn the idea into a real, running project fast — for ~220 records it handles vectors *and* metadata filtering with near-zero setup. The right call for V1's "ship and learn" phase.
**Tradeoff:** It stops being the answer as the data grows. Beyond a small dataset I'll want a real database — relational structure, joins, and transactional consistency across onsen, hotels, regions, and (later) user preferences — not just a vector store with metadata bolted on. So I kept the seam open (a reserved `schema.sql`) to migrate to **pgvector** — vectors *and* a proper relational database in Postgres — when the data actually demands it, rather than paying that complexity now.

---

## Engineering challenges & how I solved them

Most of these were **correctness** and **production** problems — the hard part of shipping an LLM to users, not "make it talk." (Full write-up in [`PROJECT_JOURNEY.md`](./PROJECT_JOURNEY.md).)

### 1. The LLM fabricated data it didn't have
When retrieval returned nothing, GPT-4o cheerfully **invented** plausible onsen and hotels from its training data — real-sounding names, fake details, even `example.com` URLs.
**Diagnosis:** classic ungrounded generation — the model treats "no context" as "improvise."
**Fix:** I treat the LLM as **untrusted for facts**. Anti-fabrication guardrails live in *both* the system prompt *and* the structured-output schema: every onsen/hotel must come verbatim from tool output; if a tool returned nothing, the list must be empty and the reply must say so; missing fields stay `null` rather than invented. The agent is now honest about "no results."

### 2. RAG semantic search ignored location
Pure vector similarity would return an Okinawa onsen for a "Tokyo" query — embeddings capture *vibe*, not *place*.
**Fix:** a ChromaDB metadata `where` filter on `prefecture_en`, with the agent extracting the prefecture from the user's message. **Semantic ranking *within* a hard location constraint.**

### 3. App and ingest wrote to different databases (prod-only bug)
In the Railway container, `/chat` returned zero results despite a "successful" ingest — the app read an empty ChromaDB while the ingest job had written to a different path. The two computed the path independently.
**Fix:** made `settings.chroma_path` the single source of truth and had the ingest job import the **same** `get_collection()` the app uses, so they can't diverge — with regression tests asserting they resolve to the same path.

### 4. Deterring abuse of a public endpoint that spends money
Once deployed, `/chat` was publicly callable, and every call triggers paid GPT-4o + embedding requests — an open door to cost abuse.
**Fix:** a **fail-closed** `X-API-Key` guard as a reusable FastAPI dependency on `/chat` and `/hotels` (`/health` stays open for platform checks), with constant-time comparison. If the key is unset, every guarded request is rejected — never silently allowed.
**The honest caveat:** this is a **deterrent, not authentication.** The frontend ships the key as a build-time `VITE_` var, so it's baked into the JS bundle and visible in the Network tab — a client-side SPA can't hold a real secret. It stops drive-by bots and casual `curl`, not a determined user with devtools open. Real cost protection is server-side and can't be bypassed by the client: **rate limiting + spend caps + real user auth** (on the V2 hardening track).

> More in the journey doc: brittle ingestion on real-world data, production papercuts (CORS, Vercel monorepo, build-time env inlining), and over-rigid tests.

---

## Known limitations — and the path forward

I'd rather name the gaps than pretend they don't exist. **These are conscious tradeoffs for V1, with clear paths forward.**

- **Per-request geocoding is the latency bottleneck.** The dataset has no coordinates, so the agent geocodes every result at request time (parallelised, but still N Google calls). → **Fix:** geocode once at ingest, store `lat`/`lng` in ChromaDB metadata, drop runtime geocoding entirely.
- **Chat history is in-memory.** Lost on restart, not multi-instance safe. → **Fix:** a persistent session store (Redis).
- **Hotel names/details surface in Japanese on the map-click path.** This directly undercuts the product's whole promise — *information in English* — and could put off the English-speaking users it's built for. The Rakuten API returns Japanese-only data; the conversational `/chat` path has the agent translate it, but the deterministic `/hotels` endpoint (the map click) deliberately skips the agent for speed (see Architecture), so its hotels come back untranslated. A real tradeoff of that optimization. → **Fix:** a dedicated `translation_service` that translates hotel name/details and **caches by Rakuten hotel id**, so *both* paths get English cheaply without re-translating on every fetch.
- **No eval harness yet.** Behaviour is verified with manual + smoke tests. → **Fix:** a fixed evaluation set measuring retrieval hit-rate, fabrication rate, and answer quality — so "is it good?" has a *number*.
- **No observability.** No tracing or token/cost/latency accounting. → **Fix:** structured per-request logging + tracing (e.g. LangSmith).
- **The API key is a deterrent, not auth.** It's baked into the client bundle (a SPA can't hold a secret), so it stops casual abuse but not a determined user. → **Fix:** rate limiting + a hard spend cap on the OpenAI key + real user auth issuing short-lived server-side tokens.

---

## Roadmap

**V2 — Intermediate (next)**
- Performance: ingest-time geocoding (kill per-request Google calls) + measured before/after latency; cache query embeddings.
- New services: `booking_service`, `preferences_service`, `translation_service`.
- Observability (LangSmith tracing) and an eval harness (e.g. RAGAS).
- Product: richer map filters that actually constrain results; user preference memory.
- **Agent: slot-filling on a LangGraph `StateGraph`** — gather required parameters before retrieving, for predictable, cheaper, more accurate conversations ([design doc](./docs/v2-slot-filling-agent.md)).
- Hardening: rate limiting, retries/timeouts on external calls.

**V3 — Advanced**
- Multi-agent: an orchestrator coordinating specialised search / rank / personalise agents via LangGraph.
- Migrate chat from GPT-4o to Claude Sonnet.
- pgvector migration when scale demands it.
- **Azure migration** (Azure OpenAI Service) for an enterprise-grade deployment story.

---

## Run it locally

Two servers run side by side, each from its own directory.

**Backend** — FastAPI on port 8000:
```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
.venv/bin/uvicorn api.main:app --reload --port 8000
```
Requires `backend/.env` (see [`.env.example`](./.env.example) at the repo root):
```
OPENAI_API_KEY=...        # embeddings + GPT-4o
GOOGLE_MAPS_API_KEY=...    # geocoding
RAKUTEN_APP_ID=...         # Rakuten Travel API
RAKUTEN_ACCESS_KEY=...
API_KEY=...                # the X-API-Key guard
```
Health check: `GET http://localhost:8000/health`.

**Frontend** — Vite + React on port 5173:
```bash
cd frontend
nvm use            # Node 20.16.0 (Vite 5 needs Node 18+)
npm install
npm run dev
```
Requires `frontend/.env`:
```
VITE_API_URL=http://localhost:8000
VITE_GOOGLE_MAPS_API_KEY=...
VITE_API_KEY=...           # matches the backend API_KEY
```

**Tests:** `pytest` in `backend/` (76 tests, external I/O mocked) · `npm test` in `frontend/` (111 Vitest + RTL tests).

---

## Stack

**Backend:** FastAPI · LangGraph ReAct agent · GPT-4o + `text-embedding-3-small` · ChromaDB · Pydantic
**Frontend:** React 18 · Vite 5 · Tailwind · `@react-google-maps/api`
**Data:** ~220 onsen (Okinawa + Tokai), Japanese→English translated at ingest via `gpt-4o-mini`, embedded with prefecture/city/spring-type metadata
**Integrations:** Google Maps (geocoding + JS map) · Rakuten Travel API (live hotels) — displayed with the required [Rakuten Web Service credit badge](https://webservice.rakuten.co.jp/guide/credit) per their attribution terms
**Infra:** Railway (backend, persistent ChromaDB volume) · Vercel (frontend) · GitHub Actions CI

---

*V1 is live in production and feature-complete for its scope. Not stopping here — V2 is next, starting with the geocoding performance work. Full build narrative: [`PROJECT_JOURNEY.md`](./PROJECT_JOURNEY.md).*
