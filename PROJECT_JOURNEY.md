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

### 9. I instrumented the loop and attributed the latency to the exact LLM calls
**Problem:** "The LLM loop is the bottleneck" was still a hand-wave. To justify the V2 workflow redesign I needed to know *which* calls cost what — instrument → baseline → (later) show the delta.
**Solution:** Wired LangSmith step-level tracing into the existing GPT-4o ReAct agent (off by default, fail-safe; enabled via `LANGSMITH_*` env vars surfaced through `core/config.py`). No agent refactor — just `stream_usage=True` for token capture and a named/tagged run config. Then I replayed `"find me 20 onsens in Shizuoka"` and read the per-step timeline straight off the request log.
**The attributed baseline (one 30 s run; the query returns 20 onsen):**

| Step | Graph node | Time |
|---|---|---:|
| LLM call #1 | agent node — decide to call `search_onsen(prefecture=Shizuoka)` | 1.2 s |
| embeddings + Chroma | tools node — retrieve 20 records | ~0.6 s |
| **LLM call #2** | agent node again — **"observe" the 20 records** (ReAct routing: am I done?) | **16.8 s** |
| **LLM call #3** | structured-output node — **re-serialize 20 records to JSON** (forced by `response_format=AgentResponse`) | **11.6 s** |

**Finding:** ~28 s of the 30 s is two GPT-4o round-trips that route the 20 retrieved records *through* the model — once to "observe" (#2), once to coerce into the response schema (#3). Both are configured on a single line: `create_react_agent(llm, tools, response_format=AgentResponse)`. Two distinct redundancies fall out: **#3 is redundant because the data is structured** (assemble `onsens[]` in Python), and **#2 is redundant because the control flow is predictable** for the dominant single-hop query (replace LLM routing with `if user_wants_hotels: …`). #2 isn't useless in principle — it's the tool-chaining router — so the workflow *replaces* it with explicit code rather than deleting the capability. This is the measured case for V2: collapse 3 round-trips → ~1, plausibly ~30 s → ~3–5 s, and remove the fabrication surface structurally. The high variance (16 s observed from Postman vs 30 s here) is itself an argument for fewer, smaller LLM calls.

### 10. I shipped the workflow redesign — and the predicted delta landed
**Problem:** Challenge #9 ended on a *prediction* (~30 s → ~3–5 s), not a result. The redesign had to be built, measured against the *same* query, and rolled out without a risky big-bang cutover.
**Solution:** Replaced the ReAct loop with a deterministic **workflow** for the dominant path, behind a `CHAT_ENGINE=react|workflow` env flag — same `/chat` contract, instant rollback, and a real A/B seam. The workflow keeps exactly **one** LLM call (`parse_intent`, on the cheaper `gpt-4o-mini`) to extract `{prefecture, query, wants_hotels}`, then assembles `onsens[]` in pure Python from Chroma metadata; hotels are a conditional code branch, not an LLM routing decision. Baseline calls #2 (observe) and #3 (JSON re-serialize) are gone.
**The measured A/B (same `"find me 20 onsens in Shizuoka"`; both return 20 grounded onsen):**

| Engine | LLM round-trips | Latency |
|---|---:|---:|
| ReAct (v1 baseline) | 3 | **35.3 s** |
| Workflow (v2) | 1 | **3.47–3.76 s** |

**Result:** ~**10× faster**, and #9's prediction held. The win isn't only speed: removing the LLM from the data-assembly path kills the fabrication surface *structurally* — the model can't invent onsen it never assembles. Shipped flag-gated, validated in prod via the A/B, then **cut over to `workflow` as the live engine** (ReAct retained behind the flag for rollback — later removed entirely in V3 once the workflow was proven). The next LLM call to earn its place back is the `analyze_onsen` judgment layer — the one step where weighing trade-offs is genuinely a model's job.

### 11. A smoke test caught a flaky RAG bug that unit tests couldn't — and the fix was a retrieval-design decision
**Problem:** After building the `ask`-mode knowledge base (semantic RAG over prose docs), the unit suite was fully green (269 passing), but the first real smoke test was alarming: in-KB questions like *"Do onsen allow tattoos?"* **intermittently** returned the "I don't have that information" fallback. Re-running the *same* question gave **1 success in 5** — a flake, which mocked tests can't surface because they stub out retrieval and the LLM.
**How I debugged it:** Treated the path as a pipeline and binary-searched it. Checked raw Chroma distances (0.22–0.27 — fine, hypothesis "threshold too tight" rejected); called `query_knowledge` and `answer_question` in isolation (both correct); so the bug was *above* the node. Instrumenting the full path showed the workflow calls `parse_intent` — an LLM — *internally*, and feeds its **reformulated** query to retrieval. Measuring distances across six reformulations of one question exposed the variance: most landed ~0.25, but a weaker phrasing (`'onsen tattoo policy'`) hit **0.49–0.58**, tipping past the `0.55` cutoff and filtering everything out. The **original message** retrieved reliably at ~0.22.
**The deeper finding:** a distance threshold *cannot* cleanly gate relevance here — measured in-KB vs off-KB distances **overlap** (a legitimate *"Can I wear a swimsuit?"* sits at ~0.65, while off-topic *"what's the wifi password?"* is ~0.47, i.e. *closer*). So distance is the wrong tool for "is this answerable"; the **grounding prompt** is — the LLM reads the top-k chunks and itself returns the fallback when they don't answer the question.
**Solution:** Retrieve with the **original message** (the truest semantic-RAG signal; reformulation was built for *structured search*, not prose Q&A), and demote the distance ceiling to a loose coarse guard (0.55 → 0.85), letting the grounded prompt make the no-info call.
**Result:** Deterministic — 5/5 on the previously-flaky question, 7/7 across an in-KB/off-KB smoke (real questions answered, "wifi"/"stock price" correctly refused), unit suite 269 green, and the LangSmith eval 7/7 on grounding/structure/cost/latency. The lesson: **green unit tests prove the wiring, not the behavior** — RAG quality is an empirical property you only see by running real queries against real data, and "how should retrieval decide it doesn't know?" is a design decision, not a threshold to tune.

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

I'd rather name the gaps than pretend they don't exist. This section began as V1's honest limitations; **most have since been closed through V2/V2.5**, so I've kept it as a scorecard rather than pretending they were never there.

**Closed since V1:**
- **Eval harness** — grew from the seeded fabrication slice into a full LangSmith flow-eval over the real workflow (grounding, per-mode structure, cost, latency), now a **deterministic CI release gate**.
- **Observability** — LangSmith step-level tracing in prod with per-request token/cost accounting, sliced by mode.
- **Performance** — the ReAct→workflow redesign (challenge #10) landed the measured ~10× win.
- **Resilience** — outbound retries/backoff and timeouts on the external calls (Rakuten/Google/OpenAI); rate limiting on the paid endpoints.
- **Guardrails asserted** — the no-fabrication contract is pinned by tests + the eval grounding evaluator, not just smoke-level.
- **Frontend tests in CI** — Vitest now gates PRs alongside backend pytest.
- **Packaging** — README with demo GIF + live link and architecture/sequence diagrams shipped.

**Still open (conscious tradeoffs):**
- **State & scale.** Chat history / session state now persists (V3 Step 0), but the container is still **single-worker** and the Postgres checkpointer path is unimplemented until Railway Postgres lands — so it isn't multi-instance safe yet. Dataset is ~220 records; pgvector seam kept open for when scale demands it.
- **Pros/cons groundedness isn't gated.** The **LLM-as-judge** evaluator is built but **parked** (non-deterministic false-negatives blocked clean releases); re-enabled once V3 + real Google Places ratings stabilise the signal.
- **Map-click hotels surface in Japanese** — the deterministic `/hotels` endpoint skips the translating agent for speed; the fix is a `translation_service` that caches by Rakuten hotel id.

---

## Roadmap

### V2 / V2.5 — Intermediate (delivered & live)
The V2 discipline was *scaffolding around the agent first* — **instrument → baseline → change → show the measured delta** — not building a fancier agent. All of it shipped:
- **Performance:** ingest-time geocoding + the ReAct→workflow redesign (challenge #10) — measured ~10× win.
- **The "Guide" brain:** the `analyze_onsen` step — per-onsen **pros/cons** + a grounded recommendation over a compact projection of the retrieved candidates (the one LLM call that earns its place back). Live via `ANALYZE_ENABLED`.
- **`ask` knowledge base:** semantic RAG over prose docs (etiquette, tattoo policy, spring-type benefits) in a separate Chroma collection. Live via `ASK_ENABLED`.
- **Rigor:** LangSmith tracing + per-request cost accounting; a LangSmith eval harness now a **deterministic CI release gate**; rate limiting + outbound retries/backoff; frontend Vitest in CI.

Still-open V2-era ideas carried forward: `translation_service` (cache hotel-name translations by Rakuten hotel id), richer map filters, and expanding coverage beyond Okinawa + Tokai.

### V3 — Advanced (under active build)
The trip-planner **agent** — the concrete query the workflow can't serve (*"plan a 3-day onsen trip"*): dynamic tool sequencing + re-planning. **Single agent first; multi-agent only if it strains.** PR1–PR5 are **merged to `develop` behind a `trip_enabled` flag** (not yet flipped in prod). Full design: [`docs/v3-trip-planner-plan.md`](./docs/v3-trip-planner-plan.md).
- ✅ **Step 0 — persistent session state:** a session store checkpointing per-thread state (SQLite local; the LangGraph `PostgresSaver` prod path lands with Railway Postgres — currently raises `NotImplementedError`).
- ✅ **Slot-filling** (regions · nights · dates · party · budget · prefs) via a LangGraph agent, plus region validation (reject non-Japan early), a naive day-by-day itinerary, per-stop hotels (fail-soft), and multi-turn/trajectory evals against the workflow baseline.
- ⏭️ **Next (both gated on Google billing):** **Places** ratings/reviews to *ground* pros/cons in real signal (this *is* the parked `ratings_service`) — PR6 · **Distance Matrix/Directions** for travel-time + re-planning, the real agent loop — PR7.
- ⏭️ **Model:** migrate chat GPT-4o → **Claude (Sonnet / Opus)** with a provider fallback chain; pgvector when scale demands it.
- **Multi-agent:** only *later* — an orchestrator over specialised sub-agents — when a single agent visibly strains, not before.

### Guiding principle
Each addition is self-contained: a new external API is a new `services/{name}`, a new agent capability is a new `tools/{name}`, a new endpoint is a new `routes/{name}`. The layering keeps it from collapsing under its own weight.

---

## Design note — the autonomy ladder (captured June 2026; since delivered)

A June design discussion on where the system goes after V1. **Most of what it proposed has since shipped** — the `ask`-mode knowledge base, the `analyze_onsen` recommend brain (grounded on a compact projection of the retrieved candidates), and the two-collection split (onsen records vs. a *separate* prose KB, with a small spring-type→benefit lookup table rather than embeddings) are all **live in prod**. What's worth keeping is the framing that still drives every "workflow or agent?" call.

### The autonomy ladder
The system walks **up an autonomy ladder**, one rung at a time:

> **rules → pipeline → workflow → agent → multi-agent**

Use the **least autonomy that solves the task**, and climb a rung only when a *concrete* case can't be served below — not because "agent" sounds advanced. That's the honest arc: V1 jumped straight to a ReAct **agent** (right while query shapes were unknown); I **measured** it was over-reach and graduated *down* to a deterministic **workflow** (challenge #10, ~10×); now I climb back *up* to an agent **only for the one case that needs it.** "I measured my agent was the wrong altitude and graduated down, then back up for a real reason" beats "I built an agent."

**The case that defines the boundary** was a real test query: *"I'd like a 3-day onsen trip — what's your suggestion?"* — the first query whose tool sequence **isn't knowable up front** (how many onsen, which regions, hotels per night, transport, re-plan if one's full). That dynamic, looping, re-planning shape is exactly when autonomy earns its cost. Everything below it stays a workflow:

| Query | Mode | Rung |
|---|---|---|
| "Onsen in Shizuoka" | **search** | workflow (deterministic list) |
| "Somewhere relaxing with outdoor baths" | **recommend** | workflow (candidates → analyze) |
| "Do they allow tattoos? What do I bring?" | **ask** | workflow (semantic RAG over KB) |
| **"3-day onsen trip, suggestions?"** | **agent** | a real itinerary — sequencing, transport, re-planning — **= V3, now under active build** |

So `search` / `recommend` / `ask` are correctly **pre-wired workflows**; the trip-planner is the concrete scenario that becomes the **V3 agent** (multi-agent only *later*, for "compare these regions and plan"). The agent comes back when *query complexity* demands it — not the résumé.

**Grounding discipline (still enforced):** onsen records ground the **FACTS** (any claim about a specific onsen comes from its own data); the KB grounds the **REASONING** (domain knowledge to interpret a need — "skin → sulfur" — never to assert a new fact about a specific onsen). Real **Google Places ratings** (V3 PR6) are the next lever, to ground pros/cons in signal rather than LLM inference — paired with the parked LLM-as-judge evaluator once the flow + data stabilise.

---

## Status

V1 is **live in production and feature-complete** for its scope. V2's performance headline shipped and is **live in prod**: ingest-time geocoding plus the ReAct→workflow redesign (challenge #10) — ~10× faster and the workflow is now the **only** `/chat` engine (in V3 the legacy ReAct agent and the engine-select flag were removed, since the workflow had been the prod engine through V2/V2.5/ask). V2.5's **3-mode router**, the **`analyze_onsen` "guide" judgment layer**, AND the **`ask`-mode knowledge base** are all **live in prod** (`ANALYZE_ENABLED=true`, `ASK_ENABLED=true`; frontend renders pros/cons + recommendation). The LangSmith eval harness is now a **deterministic CI release gate** (name-grounding, structure, cost, latency); the **LLM-as-judge** evaluators are **parked** — judging LLM prose while the flow + data are still moving produced non-deterministic false-negatives that blocked clean releases, so they're kept for re-enable once V3 + real ratings stabilise the signal.

**V3 — the trip-planner agent — is under active build**, with **PR1–PR5 merged to `develop` behind a `trip_enabled` flag** (not yet flipped in prod): Step 0 persistent session state, the LangGraph agent + slot-filling, region validation, a naive itinerary, per-stop hotels (fail-soft), and multi-turn evals. The suite sits at **418 backend + 126 frontend tests**. **Next:** decide **PR6** (Google Places ratings to ground pros/cons) vs **PR7** (Distance Matrix/Directions routing + re-planning — the real agent loop); both gated on Google billing. Carried prerequisites before any prod cutover: `session_id`→per-conversation UUID and a Railway Postgres checkpointer (the `PostgresSaver` path currently raises `NotImplementedError`). Full design: [`docs/v3-trip-planner-plan.md`](./docs/v3-trip-planner-plan.md).
