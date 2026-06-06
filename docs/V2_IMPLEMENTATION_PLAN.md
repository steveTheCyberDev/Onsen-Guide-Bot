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
