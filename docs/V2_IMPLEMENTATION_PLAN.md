# V2 Backend Redesign — Implementation Plan

Status: **planned** (2026-06-06). Builds on the measured baseline in
`PROJECT_JOURNEY.md` challenge #9.

> **Resequenced 2026-06-06:** analyze (Step 3/⑤) is deferred to the END of the
> pipeline. `run_workflow` (Step 4) ships with analyze as a gated seam (a
> commented no-op hook), NOT implemented — keeping the response contract
> identical to v1 for a clean A/B.

## Why

Tracing attributed `"find me 20 onsens in Shizuoka"` (~30s) to two redundant
GPT-4o round-trips that route the 20 retrieved records *through* the model:

| Step | Node | Time |
|---|---|---:|
| LLM #1 | agent — decide to call `search_onsen` | 1.2s |
| retrieval | embeddings + Chroma | 0.6s |
| **LLM #2** | agent — "observe" the 20 records (ReAct routing) | **16.8s** |
| **LLM #3** | structured-output — re-serialize 20 records to JSON (`response_format=AgentResponse`) | **11.6s** |

~28s of 30s is overhead. Both behaviours come from one line in
`agent/agent.py`: `create_react_agent(llm, tools, response_format=AgentResponse)`.

## Target architecture — explicit WORKFLOW (not LangGraph; that's V3)

A plain `async def` pipeline. Two layers:

- **DATA layer (Python, deterministic):** assemble `onsens[]` from Chroma
  metadata. Facts. No fabrication.
- **JUDGMENT layer (LLM, only where judgment is genuine):** intent parsing,
  pros/cons analysis, hotel translation.

```
run_workflow(message)
  ① parse_intent(message)          LLM (small)   → {prefecture, query, wants_hotels}
  ② query_onsen_structured(...)    Python        → onsens[]   (no LLM; kills #3 + fabrication)
  ⑤ analyze_onsen(intent, onsens)  LLM (judgment, gated) → per-onsen pros/cons + recommendation
  ③ if wants_hotels and onsens:    code branch   → search_hotels → translate_hotels
  ④ reply = template               no LLM
```

## Resolved design forks (overridable)

| Fork | Default |
|---|---|
| pros/cons shape | per-onsen `pros[]/cons[]` **and** one top-level `recommendation` |
| analyze on/off | **off** for A/B #1 (isolate retrieval win); on for A/B #2 |
| reply | **template** first; richer summary reuses analyze output later (no 4th call) |
| intent model | **`gpt-4o-mini`** (`intent_model` knob); analyze uses `analyze_model` (default `gpt-4o`) |

## Steps (each a small, green PR into `develop`)

### Step 1 — `query_onsen_structured()` (pure Python, no LLM)
`services/retrieval/retrieval_service.py`. Sibling of `query_onsen` returning
`list[dict]` built from Chroma metadata: `name` (name_en→name), `location`
(`"{city_en}, {prefecture_en}"`), `spring_type`/`spa_quality` (spa_quality_en),
`sales_point` (**sales_point_en** — not surfaced today), `description` (doc),
`detail_url`, `lat`/`lng` (both-or-neither guard, else None). Empty → `[]`.
Keep `query_onsen` (string) untouched. Tests in `test_retrieval_service.py`.

### Step 2 — `parse_intent()` (small LLM, structured output)
`agent/workflow/intent.py`. `ChatOpenAI(model=settings.intent_model)
.with_structured_output(Intent)` where `Intent = {prefecture: str|None, query:
str, wants_hotels: bool}`. Replaces ReAct LLM #1 + #2. Add `intent_model`
(env `INTENT_MODEL`, default `gpt-4o-mini`) to `core/config.py`.

### Step 3 — `analyze_onsen()` (the guide layer; the one LLM call that earns its place)
`agent/workflow/analyze.py`. LLM over a **compact projection** (name,
spring_type, location, short spa_quality — NOT description/coords/urls) →
`GuideResult{analyses:[{index, pros[], cons[]}], recommendation}`. Merge
pros/cons back by index. Grounded prompt (derive from provided fields only).
Add `pros: list[str]=[]`, `cons: list[str]=[]` to `OnsenResult`; add
`recommendation: str|None=None` to `AgentResponse`. Add `analyze_model`
(default `gpt-4o`) + `analyze_enabled` (default `false`) to config.

### Step 4 — `run_workflow()` pipeline
`agent/workflow/pipeline.py`. Wires ①②⑤③④ (see diagram). `search_hotels` is
sync → call via `asyncio.to_thread`. Template reply with "none found" branch.
Emits LangSmith `run_name="chat-workflow"`, `metadata.version="v2-workflow"`
for A/B vs `v1-baseline`. Returns the same dict shape as `run_agent`.

### Step 5 — `chat_engine` feature flag (keep ReAct runnable)
Add `chat_engine` (env `CHAT_ENGINE`, `react`|`workflow`, default `react`).
Rename current `run_agent` internals → `run_react_agent`; new `run_agent`
dispatches on the flag. `api/routes/chat.py` stays **unchanged**. This is the
A/B + instant-rollback seam.

### Step 6 — `translation_service` seam
`services/translation/translation_service.py` (LangChain-agnostic, cache by
Rakuten hotel id). `agent/workflow/hotels.py::translate_hotels`. Ship
**passthrough first** (Japanese in → out, behaves like today); LLM+cache impl
is a follow-up so it never blocks the latency win.

## Rollout

1. Land Steps 1–6 behind `chat_engine="react"` (dead code in prod, no API change).
2. A/B #1: staging `CHAT_ENGINE=workflow`, `ANALYZE_ENABLED=false`; fire the
   canonical Shizuoka query; compare `v2-workflow` trace vs saved `v1-baseline`.
   Expect ~28s of routing → low-single-digit seconds.
3. A/B #2: `ANALYZE_ENABLED=true`; measure the guide call's cost vs value. Run
   `eval_fabrication.py` against the workflow to confirm grounding holds.
4. Cutover: `CHAT_ENGINE=workflow` in Railway env. Keep ReAct one release for
   rollback; remove ReAct + the ~60-line anti-fabrication prompt in a final
   cleanup PR.

## Tests that will break
- `test_agent.py` coord/geocode tests patch `agent.graph.ainvoke` — stay valid
  for the react path; revisit `geocode_location in agent.tools` only at the
  ReAct-removal PR.
- `test_chat_route.py` unaffected (additive optional fields).
- New suites: workflow intent/analyze/pipeline, engine flag, translation.

## Open risks
1. **Prefecture string match** — Chroma `where` needs exact English prefecture;
   mini may emit "Shizuoka Prefecture". Normalize/validate against known set.
2. **Hotels with no coords** — recommend skip + note, not re-introduce
   per-request geocoding.
3. **analyze_model → Claude Sonnet** later — deps already present; decide by eval.
4. **CLAUDE.md folder tree is stale** — data files are `tokai_springs.jsonl` +
   `okinawa_springs.jsonl`, not `okinawa_onsen.jsonl`.

---

# V2.5 — Knowledge Base + Recommendation Agent (design, 2026-06-07)

Status: **design captured, not built.** Direction set by Steve; pending a
structure diagram + discussion before implementation. Step 5 (translation) and
the multi-turn elicitation flow are intentionally PAUSED — build the **split
(router)** and the **analytic agent** first, reasoning over existing data, with
hand-crafted test messages (no preference elicitation yet).

## The shift: from one search path to a 3-mode router
The deterministic search workflow stays as-is. Two new intents join it. A
**router** (extend `parse_intent` to also return `mode`) classifies the query
and branches:

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
   [built OK]                  (LLM: rank + pros/cons  [Layer 2, new]
                               grounded in prefs +
                               Layer-2 knowledge)
                             -> onsens + recommendation
                             [analyze_onsen, un-paused]
```

- **search** — deterministic structured query + Python assembly. Already built.
- **recommend** — retrieve candidates (Layer 1), then the **analytic agent**
  (single analytical LLM call for the MVP, over a compact projection +
  preferences) returns a ranked recommendation + per-onsen `pros[]`/`cons[]`.
  This is `analyze_onsen` (formerly deferred Step 6), now the recommend brain.
- **ask** — semantic RAG over knowledge documents (Layer 2). New capability the
  bot cannot do today.

Output: recommend/ask paths add `pros[]`/`cons[]` (per onsen) + a top-level
`recommendation` string, additive; search leaves them empty.

## Three-layer knowledge architecture — with the key correction
Steve's note proposed one vector store where "best chunks win regardless of
origin." **That is right for prose, wrong for structured onsen facts.** We
deliberately moved onsen retrieval to deterministic structured queries (kills
the serialization cost + fabrication). So:

- **Layer 1 — structured onsen data (JSONL -> Chroma METADATA, queryable/filterable).**
  Enrich with fields the analytic agent needs: `facilities` (rotenburo / indoor /
  sauna), `mixed_bathing`, `day_trip_available`, `best_season`,
  `nearby_attractions`, `access`, `english_friendly`, (cautiously)
  `allows_tattoos`, `price_range`. These are **filters**, not similarity matches —
  keep them structured; extend `query_onsen_structured`. **NOT** dumped as
  "best chunks win" text.
- **Layer 2 — knowledge documents (Markdown -> semantic RAG).** Etiquette, onsen
  types, tattoo policies, spring-type benefits, travel guides, ryokan guide,
  seasonal guide. This IS the "best chunks win" model — correct here. Powers the
  **ask** mode AND feeds the analytic agent's explanations ("sulfur is good for
  skin" cited from `spring_types.md`). **Separate ChromaDB collection** (or a
  `doc_type` metadata filter) so an onsen *search* never retrieves an etiquette
  chunk and vice-versa.
- **Layer 3 — crawled web content (periodic).** Deferred (V2-late / V3):
  robots.txt / ToS risk (japan-guide, Rakuten likely restrict), freshness,
  crawl orchestration. Don't let it block Layers 1-2.

## Priority, risks, sequencing
- **Build first: Layer 2 + the analytic-agent MVP (can run in parallel).** Layer 2
  is small, isolated, high-impact, Claude-draftable; the analytic MVP runs over
  existing description text (no enrichment needed to prove the flow).
- **Then: Layer 1 enrichment, incrementally.** Start with safely-inferable fields
  (facilities/outdoor, day-trip, vibe). **Reliability caveat:** do NOT LLM-guess
  high-stakes fields — `allows_tattoos` and `price_range` are where a wrong answer
  ruins a trip; mark `unknown` unless sourced.
- **Defer: Layer 3 web scraping** (ToS/robots/operational).
- Current data is ~220 records (`okinawa_springs.jsonl` + `tokai_springs.jsonl`),
  multi-region — enrichment fields are NOT present today; acquiring them is real
  work (LLM extraction pass and/or re-scrape).

## First concrete builds (pick after the diagram discussion)
1. **Router** — extend `parse_intent` -> `mode: search|recommend|ask`; branch the pipeline.
2. **Analytic agent** — `analyze_onsen` over candidates + preferences -> recommendation + pros/cons (single LLM call, compact projection).
3. **Layer 2 knowledge** — author the markdown docs (Claude-drafted), ingest into a SEPARATE collection, wire the **ask** branch (semantic RAG).
