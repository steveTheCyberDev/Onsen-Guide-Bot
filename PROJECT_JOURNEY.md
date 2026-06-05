# Onsen Guide Bot — Project Journey

> **Find your perfect Japanese hot spring — in English.**
> An AI agent that helps English-speaking travellers discover Japanese onsen (hot springs) and nearby hotels, through a conversational chat + interactive map.

**Live:** Frontend → https://onsen-guide-bot.vercel.app · Backend API → https://onsen-guide-bot-production.up.railway.app (auth-gated)

---

## Why I built this

I wanted a project that was more than a toy LLM wrapper — something with a real data pipeline, a retrieval system, external API integrations, a production deployment, and the messy correctness problems that come with putting an LLM in front of users. Onsen are close to my heart, and the "English info is scarce" gap is real, so it doubled as something I'd actually use.

This is a portfolio piece, but it's not a throwaway demo — I intend to take it through V2 and V3. It doesn't have to be perfect; it has to keep getting better.

---

## V1 — What I built (shipped & live in production)

### Architecture
A clean, layered backend with strict boundaries, fronted by a React SPA:

```
React (Vite + Tailwind) ──HTTP──> FastAPI
                                     │
                              LangGraph ReAct agent (GPT-4o)
                                     │  tools (thin wrappers)
            ┌────────────────────────┼────────────────────────┐
     search_onsen            geocode_location          search_rakuten_onsen
            │                        │                          │
   retrieval_service        geocoding_service           rakuten_service
   (ChromaDB RAG)           (Google Maps)               (Rakuten Travel)
```

**Layering rules I enforced:** data flows downward only (`api → agent → tools → services`); `services/` stay framework-agnostic (no LangChain imports) so they're swappable; tools are thin wrappers with no business logic; `core/config.py` is the single source of truth for settings. One deliberate exception: the deterministic `POST /hotels` endpoint (a map click → coordinates-in/hotels-out lookup) calls the service directly, skipping the agent to avoid needless LLM latency and cost.

### Stack
- **Backend:** FastAPI, LangGraph ReAct agent, GPT-4o (chat) + `text-embedding-3-small` (embeddings), ChromaDB vector store.
- **Data:** a scraped/translated onsen dataset (~220 records across Okinawa + the Tokai region), Japanese → English translated at ingest via `gpt-4o-mini`, embedded into ChromaDB with prefecture/city/spring-type metadata.
- **Frontend:** React + Vite + Tailwind, a 3-panel layout (chat / Google Map / hotel list) driven by a `useReducer` state machine, `@react-google-maps/api`.
- **Integrations:** Google Maps (geocoding + JS map), Rakuten Travel API (real hotels near an onsen).
- **Infra:** Backend containerised on **Railway** (persistent volume for ChromaDB); frontend on **Vercel** (monorepo root = `frontend/`); GitHub Actions CI runs the backend test suite on PRs; Vercel Web Analytics.
- **Tests:** 76 backend (pytest, external I/O mocked) + 111 frontend (Vitest + React Testing Library), green.

### Features
- Conversational onsen search ("find me a sulfur onsen in Shizuoka") with location-aware retrieval.
- Results plotted on an interactive map; click a chat result to centre its marker.
- "See nearby hotels" → real Rakuten listings with prices, images, and booking links.
- English-first throughout, with original Japanese names preserved.

---

## Engineering challenges & how I solved them

This is the part I'm most proud of — most of these were *correctness* and *production* problems, not "make the LLM talk."

### 1. The LLM fabricated data it didn't have
**Problem:** When retrieval returned nothing (or the Rakuten tool returned no hotels), GPT-4o happily *invented* plausible-looking onsen and hotels from its training knowledge — e.g. asking for Shizuoka onsen returned famous real names (Atami, Shuzenji) that weren't in my dataset, with fabricated details and even placeholder `example.com` URLs.
**Solution:** Treated the LLM as untrusted for facts. Added explicit anti-fabrication guardrails in both the system prompt **and** the structured-output schema: every hotel and every onsen *must* come verbatim from the tool output; if a tool returned nothing, the list must be empty and the reply must say so. Map each field directly from tool output; leave missing fields null rather than inventing. This made the agent honest about "no results."

### 2. RAG semantic search ignored location
**Problem:** Pure vector similarity would happily return an Okinawa onsen for a "Tokyo" query — embeddings capture *vibe*, not *place*.
**Solution:** Added a ChromaDB metadata `where` filter on `prefecture_en`, and taught the agent to extract the prefecture from the user's message and pass it to the search tool. Semantic ranking *within* a hard location constraint.

### 3. App and ingest job wrote to different databases
**Problem:** In the Railway container, the app read an empty ChromaDB while the ingest job had written to a different (throwaway) path — so `/chat` returned zero results in prod despite "successful" ingestion. They computed the Chroma path independently.
**Solution:** Made `settings.chroma_path` (env-overridable) the single source of truth, and had the ingest job import the *same* `get_collection()` the app uses, so they can never diverge. Added regression tests that assert both resolve the same path/collection.

### 4. Brittle ingestion on real-world data
**Problem:** Real records had null `spa_quality` and empty descriptions — embedding an empty string is meaningless and can error at the embeddings API.
**Solution:** Document-building fallbacks (sales pitch → name+prefecture → constant) guaranteeing a non-empty embedding, plus graceful handling of missing fields.

### 5. Securing a public endpoint that spends money
**Problem:** Once deployed, `/chat` was publicly callable and every call triggers paid GPT-4o + embedding requests — an open door to cost abuse.
**Solution:** A static `X-API-Key` guard implemented as a reusable FastAPI dependency on `/chat` and `/hotels` (`/health` stays open for platform health checks). Constant-time comparison, and **fail-closed**: if the key is unset, every guarded request is rejected rather than silently allowing all. The frontend sends the key via a build-time env var through one centralised API helper.

### 6. Production deployment papercuts
- **CORS:** the deployed frontend was blocked until I added the exact Vercel origin to the backend's allowed origins (no trailing slash, exact match).
- **Vercel monorepo:** the project root has to point at `frontend/`, not the repo root.
- **Build-time env inlining:** `VITE_*` vars are baked into the JS bundle at build time and are publicly visible — fine for the Maps key (restricted by referrer) and the backend URL, but it shaped how I think about frontend "secrets."
- **Release flow:** prod auto-deploys from `main`, with `develop` as integration. Every change went `feature → PR → develop → release PR → main`; `main` was never touched directly.

### 7. Tests that were too rigid
**Problem:** Frontend tests asserted the `fetch` headers object *exactly*; the moment I added the `X-API-Key` header, they broke — even though the behaviour was correct.
**Solution:** Relaxed to `expect.objectContaining`, asserting the contract that matters rather than an exhaustive snapshot. A good reminder that over-specified tests punish correct change.

### 8. Performance: I fixed the "obvious" bottleneck — and measured that it wasn't one
**Problem:** The dataset has no coordinates, so the agent geocoded *every* returned onsen via a Google call at request time — up to ~20 per `/chat` after I raised the result cap to 20. The obvious latency culprit.
**Solution:** Geocode each onsen **once at ingest** and store `lat`/`lng` in ChromaDB metadata, then drop runtime geocoding. Shipped.
**The twist — I measured before *and* after.** Baseline ~22 s for a Shizuoka query; after removing runtime geocoding, ~22 s. No change. Reading the code explained why: the geocoding was already parallel (`asyncio.gather`), so it was never the dominant cost — the GPT-4o ReAct loop is (13–32 s, high variance). So the refactor was a real **cost + reliability** win (it stops re-paying Google to geocode the same static data on every request, and removes a runtime dependency) but **not** a latency win. The lesson: the "obvious" bottleneck was wrong, and only the measurement revealed it. The actual latency lever is the LLM loop — which points straight at the V2 workflow redesign.

---

## What I learned

- **LLMs are unreliable narrators.** The hard part of an AI product isn't generation — it's constraining it: grounding answers in retrieved data and making "I don't know" the default.
- **Retrieval needs structure, not just vectors.** Metadata filters + semantic ranking beat similarity alone.
- **Single source of truth or bust.** The Chroma path bug came from two code paths computing the "same" value independently.
- **Production is its own skill.** Auth, CORS, monorepo deploys, env handling, release discipline — none of it shows up in a local demo, all of it matters.
- **Measure before optimising — the obvious culprit is often wrong.** I was sure per-request geocoding was the latency bottleneck. Timing `/chat` before and after removing it showed no change; the LLM ReAct loop was the real cost. The number corrected the guess.
- **Use the least autonomy that solves the task.** I reached for an autonomous agent in V1; measuring and re-reading the flows showed they're fixed pipelines — a *workflow* with the LLM only where judgment is genuinely needed is cheaper, faster, and removes fabrication *structurally* (the LLM can't invent data it never assembles). The agent earns its keep at V3, not before.

---

## Honest limitations — and the path to production-grade

I'd rather name the gaps than pretend they don't exist. Here's what V1 deliberately doesn't do yet, and what it would take to make it production-/senior-grade. (The first three are also the top of my V2 plan — they're product improvements *and* the things that demonstrate real LLM-engineering rigor.)

**AI engineering depth**
- **Eval harness — seeded (fabrication slice).** I built a small fabrication eval (`scripts/eval_fabrication.py`): fixed cases with ground truth read from the DB — out-of-data prefectures must return *empty* (the no-fabrication contract), in-data ones must return only real onsen. It already earned its keep, catching that `gpt-4o-mini` isn't a safe drop-in (it misuses the search tool). Still to broaden: retrieval hit-rate, tool-selection accuracy, answer quality, and wiring it to gate CI — so "is the agent good?" has a fuller *number*, not an opinion.
- **No observability.** No request tracing, token/cost accounting, or latency metrics. I'd add structured per-request logging (tokens, cost, latency, tool calls) and/or tracing.
- **Performance — now measured (and the result surprised me).** Before/after timing on `/chat` (challenge #8) showed ingest-time geocoding was a cost/reliability win, not a latency one — the GPT-4o ReAct loop is the real bottleneck. Next perf work targets the loop (a workflow with fewer round-trips + a cheaper model), not geocoding. Still missing: token/cost accounting and tracing to attribute latency per step.

**Engineering rigor**
- **Resilience:** external calls (Rakuten, Google, OpenAI) lack retries, timeouts, and graceful degradation — the unhappy path isn't designed for yet.
- **Guardrail tests are smoke-level**, not asserted; I'd add tests that pin the no-fabrication behaviour against regressions.
- **State & scale:** chat history is in-memory (lost on restart, not multi-instance safe), there's no rate limiting, and the dataset is ~220 records. Each needs either a fix (persistent session store, rate limiting) or an explicit scaling plan.
- **Frontend tests aren't in CI yet** (backend pytest is); both suites should gate every PR.

**Packaging**
- A README with a demo GIF + live link, and Architecture Decision Records + a C4/sequence diagram, to make the design thinking legible.

---

## Roadmap

### V2 — Intermediate (next)

#### What to fix BEFORE starting V2
The slot-filling migration is the headline of V2, but I'm deliberately doing the *scaffolding around the agent* first — otherwise I can't prove the new agent is better, only assert it. The senior move isn't building a fancier agent; it's the loop **instrument → baseline → change → show the measured delta**. (This list is verified against current AI-engineering practice, not just my own gut.)

- **Tier 1 — unblock everything (do first):**
  - *Ingest-time geocoding* — the one measurable perf win; doing it first gives V2 a concrete before/after latency number (also a V2 feature, but really pre-work).
  - *Eval harness* — a fixed set scoring retrieval hit-rate, fabrication rate, tool-selection accuracy. Without a number I can't honestly claim slot-filling is "more accurate." The senior version is evals **gating CI** plus a loop where real failed traces become new eval cases.
  - *Agent tracing* — step-level traces (LangSmith/Langfuse, OpenTelemetry-compatible) on the *current* ReAct agent, to capture the baseline the migration is measured against.
  - *Frontend tests into CI* — uncomment the `frontend-tests` job in `ci.yml` and require both checks on `main` (trivial, overdue).
- **Tier 2 — foundation V2 leans on:**
  - *Resilience* — today only `timeout=10` exists. Add the real stack: retry with backoff + jitter, fallback chains, circuit breakers, graceful degradation, and multi-provider failover (LLM providers run ~99–99.5% uptime). The V3 GPT-4o→Claude migration is the natural hook for a fallback chain.
  - *Observability* — structured per-request logging: tokens, cost, latency, tool calls. The whole point of slot-filling is "cheaper, fewer calls" — unprovable without this.
  - *Persistent chat history* — the in-memory dict breaks on restart / multi-instance; slot-filling is *more* stateful, so this only gets worse if ignored.
- **Tier 3 — pin against regressions (can run alongside early V2):**
  - *Assert the anti-fabrication guardrails* as real tests (today smoke-level) — lock in the proudest correctness win before refactoring the agent.
  - *Rate limiting* on the paid endpoints (the API-key guard exists; add a limiter).

#### V2 features
- **Performance:** ingest-time geocoding (kill per-request Google calls); consider response streaming and a faster/cheaper model where the ReAct loop allows; cache query embeddings.
- **New services:** `booking_service`, `preferences_service`, `translation_service` (cache hotel-name translations by Rakuten hotel id instead of re-translating each fetch).
- **Product:** richer map view + filters; wire the prefecture filter to actually constrain results (today it only re-centres the map); user preference memory.
- **Agent:** move from open-ended ReAct toward slot-filling for more predictable, cheaper conversations.
- **Data:** expand coverage beyond Okinawa + Tokai to more regions.
- **Hardening:** rate limiting; consider real user auth (beyond the shared API key).

### V3 — Advanced
- **Multi-agent:** an orchestrator coordinating specialised search / rank / personalise agents, via LangGraph.
- **Communication:** API-based or event-driven between agents (instead of V1's direct function calls).
- **Model:** migrate chat from GPT-4o to Claude Sonnet (`claude-sonnet-4-6`).
- **Storage:** keep a pgvector migration path open (a `schema.sql` already reserves it) for when scale demands it.

### Guiding principle
Each addition is self-contained: a new external API is a new `services/{name}`, a new agent capability is a new `tools/{name}`, a new endpoint is a new `routes/{name}`. The layering keeps it from collapsing under its own weight.

---

## Status

V1 is **live in production and feature-complete** for its scope. I'm not stopping here — V2 is next, starting with the geocoding performance work.
