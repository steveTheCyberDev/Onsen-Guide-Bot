# V2.5 `ask` Mode — Layer 2 Knowledge Base — Implementation Plan

Status: **NOT built** — `ask` is a stub (`agent/workflow/pipeline.py`). This doc is
the build plan for it (drafted 2026-06-11). Mirrors the structure of
`V2_IMPLEMENTATION_PLAN.md`: each step is a small green PR into `develop`,
followed by "Tests that will break" and "Open risks".

KB authoring approach (decided 2026-06-11): the bot is English-facing, so we
**embed English**. Japanese source is welcome but is **translated to English at
ingest** (reusing the onsen translate-at-ingest machinery) and the Japanese is
kept as provenance metadata (`source_ja`, `source_lang`) — same-language
retrieval (EN query ↔ EN chunk) beats cross-lingual.

## Where we are (grounded in the code)

- `agent/workflow/pipeline.py:43-46` defines `_ASK_STUB_REPLY`; `pipeline.py:236-244`
  is the entire `ask` branch — it returns the stub, empty `onsens`/`hotels`,
  `recommendation=None`, logs cost, saves history, returns early. **This is the
  seam to replace.**
- `agent/workflow/intent.py:25-34,51-74` already classifies `mode="ask"` and
  returns a `query` string we can reuse as the retrieval query. No router change
  needed.
- `agent/workflow/analyze.py` is the template to mirror: module-level
  `ChatOpenAI(...).with_structured_output(...)` built once at import time, a
  strict-grounding `_INSTRUCTIONS` constant, a `run_config` with
  `run_name`/`tags`/`metadata`, `callbacks` threaded through for usage capture,
  config-gated by `settings.analyze_enabled`.
- `vectorstore/store.py` hard-codes one collection (`COLLECTION_NAME =
  "onsen_springs"`) via a module-global singleton `_collection`. It is NOT
  parameterized — a second collection needs a new accessor.
- `scripts/ingest.py` is the translate-at-ingest reference: static maps +
  `translate_batch` (gpt-4o-mini, temp 0, JSON in/out), a cache marker
  (`is_translated`), `build_document` (the embedded text), `build_metadata`, and
  `collection.upsert(ids, documents, metadatas)`. It reuses `get_collection()` so
  app and ingest agree on path (`store.py:26-29`).
- `services/retrieval/retrieval_service.py` is LangChain-agnostic and calls
  `get_collection()` directly. A KB retrieval helper belongs here, same layer.
- `core/config.py` is the single source of truth; existing knobs `analyze_enabled`
  (gate pattern, `config.py:65`), `intent_model`/`analyze_model`, `chroma_path`,
  `data_path`/`data_dir` (env-split, `config.py:130-141`).
- `agent/agent.py:106-144` owns `AgentResponse`; `api/routes/chat.py`
  `ChatResponse` mirrors it. `recommendation: str|None` is already additive there
  — the `ask` answer rides in the existing `reply` field, so **no schema change is
  strictly required**.
- `scripts/eval_flow.py:162-168` already has the `ask` example (`"What is onsen
  etiquette if I have tattoos?"`, `expected_mode="ask"`, `has_data=False`);
  `structure()` evaluator at `eval_flow.py:377-378` currently asserts `reply ==
  _ask_stub_reply()`. **This assertion must change when the stub goes away.**

---

## KB document set (Step A authors these)

Propose `backend/data/knowledge/*.md` — a sibling of the onsen JSONL data, so the
existing `data_dir` env-split (`config.py:130-141`) and the Railway `COPY data/
data/` layout cover it for free. One concern per file (clean `doc_type`
provenance, easy to cite):

| File | `doc_type` | Scope |
|---|---|---|
| `etiquette.md` | `etiquette` | Wash-before-entering, no swimwear, towel handling, quiet/no-photos, no diving, hydration. |
| `tattoo_policy.md` | `tattoo` | General tattoo stance, tattoo-friendly vs cover-up, private/family baths (kashikiri) as a workaround. NB: general guidance only — never per-onsen policy (a high-stakes field, kept `unknown` per the V2.5 reliability caveat). |
| `bathing_steps.md` | `bathing` | Step-by-step first-timer flow: undress, rinse (kakeyu), soak, rest, repeat. |
| `spring_types.md` | `spring_type` | Per spring type: what it is + commonly-cited benefits. Keys MUST match the English `spring_type` labels in onsen metadata (see Step D). |
| `ryokan_guide.md` | `ryokan` | Staying at a ryokan: yukata, tatami, kaiseki, in-room vs public baths, check-in customs. |
| `seasonal_guide.md` | `seasonal` | Best seasons, rotenburo in winter/snow, summer considerations, regional notes. |
| `what_to_bring.md` | `prep` | Towels (small + large), toiletries, hair tie, cash, what onsen provide. |

Source authoring: Claude drafts the **English** prose (the bot is English-facing).
Where a Japanese source phrase is worth preserving for citation, carry it as
`source_ja` metadata on that chunk; if a doc is authored directly in English, set
`source_lang="en"` and leave `source_ja` empty. The JA→EN translate machinery is
wired and available, but for Claude-drafted English docs there is nothing to
translate — the translate step is a no-op pass-through for `source_lang="en"`
docs and only fires for any future JA-sourced doc. This keeps the rule satisfied
without inventing Japanese to translate back.

---

## Step A — author the KB markdown docs (no code) — small PR

Add the seven `.md` files above under `backend/data/knowledge/`. Each file starts
with a tiny front-matter the ingest parser reads (`doc_type`, `source_lang`,
optional `source_ja`, optional `source_url`), then English prose under `##`
headings. No app wiring yet — pure content, reviewable on its own.

**Tests that will break:** none (content-only).

---

## Step B — config knobs for the KB collection — small PR

Add to `core/config.py`, mirroring the `chroma_path` / `data_dir` env-split and
the `analyze_enabled` gate:

```python
# Separate ChromaDB collection for Layer 2 KB prose (etiquette, spring-type
# benefits, …). Kept apart from the onsen_springs collection so an onsen search
# never retrieves an etiquette chunk and vice-versa. Override via KB_COLLECTION.
kb_collection: str = "onsen_knowledge"
# Directory holding the Layer 2 markdown docs. Default "" → computed local
# default data_dir/knowledge (CWD-independent, from __file__). Override via
# KB_DATA_PATH in prod (Railway ships them under /app/data/knowledge).
kb_data_path: str = ""
# Gate for ask mode (Layer 2 semantic RAG). False (default) → ask returns the
# safe "coming soon" stub; True → real grounded RAG answer. A/B + instant
# rollback seam, mirrors analyze_enabled. Override via ASK_ENABLED.
ask_enabled: bool = False
# Top-k KB chunks retrieved for an ask answer.
ask_top_k: int = 4
# Cosine-DISTANCE ceiling for KB chunks (Chroma returns distance, lower = closer).
# Chunks above this are dropped; if nothing survives → the "I don't know" path.
ask_max_distance: float = 0.55
# LLM that writes the grounded ask answer. Reuse intent_model by DEFAULT (cheap;
# the answer is short and fully grounded), env-overridable to a stronger model.
ask_model: str = ""   # "" → fall back to settings.intent_model at the call site
```

Add a `kb_data_dir` property mirroring `data_dir` (`config.py:130-141`): return
`Path(kb_data_path)` if set, else `data_dir / "knowledge"`.

Decision recorded — **reuse `intent_model` for `ask_model`** by default. The ask
answer is a short, fully-grounded extraction over ≤4 short chunks; gpt-4o-mini is
sufficient and keeps the ask cost budget (`eval_flow.py:78`, `ask: 0.01`)
comfortable. The dedicated `ask_model` knob exists so it can be upgraded by env
without code change.

**Tests that will break:** none; new fields have safe defaults. A `test_config`
assertion (if present) gets additive lines.

---

## Step C — separate collection + KB retrieval (services layer) — small PR

**Decision: separate collection, NOT a `doc_type` filter.** Reasoning grounded in
the code:
1. `query_onsen_structured` (`retrieval_service.py:67-71`) issues
   `collection.query(...)` with no `doc_type` guard. A single shared collection
   means every existing onsen query would need a `where={"doc_type": "onsen"}`
   added or it would start surfacing etiquette chunks — a regression risk on the
   live search path. A separate collection makes the isolation structural and
   impossible to forget.
2. The two record shapes are different (onsen metadata: `name_en`,
   `prefecture_en`, `latitude`… vs KB metadata: `doc_type`, `source_lang`,
   `heading`). Mixing muddies both `get(include=["metadatas"])` consumers —
   notably `eval_flow.py:108-121` `build_ground_truth`, which iterates ALL
   metadatas and would start ingesting etiquette "names" as onsen ground truth. A
   separate collection keeps that eval correct untouched.

Changes:

**`vectorstore/store.py`** — generalize the singleton. Add a parameterized
accessor without breaking the existing `get_collection()`:
```
get_collection(name: str = COLLECTION_NAME)  # cache per-name in a dict
get_kb_collection() -> get_collection(settings.kb_collection)
```
Keep the existing no-arg `get_collection()` behavior identical (default arg) so
`retrieval_service.py`, `ingest.py`, and `eval_flow.py` are unaffected. Same
`text-embedding-3-small` embedding fn — reuse the existing
`OpenAIEmbeddingFunction` construction.

**`services/retrieval/retrieval_service.py`** — add a sibling of
`query_onsen_structured`, staying LangChain-agnostic:
```
def query_knowledge(query: str, n_results: int, max_distance: float | None) -> list[dict]:
```
Calls `get_kb_collection().query(query_texts=[query], n_results=n_results,
include=["documents","metadatas","distances"])`, zips docs/metas/distances, drops
chunks whose distance > `max_distance`, returns records `{text, doc_type,
source_filename, heading, source_ja, source_lang, distance}`. Empty (or
all-filtered) → `[]`. This is the score-threshold that drives the "I don't know"
fallback.

**Tests that will break:** none existing. New `test_retrieval_service.py` cases
(KB query, distance filtering, empty result). `test_chroma_path.py` may assert on
`get_collection` signature — verify the default-arg change keeps it green.

---

## Step D — spring-type → benefit lookup table (no embeddings) — small PR

Per the design, a small dict, NOT a vector search. Add
`backend/agent/workflow/spring_benefits.py`:
```python
# Keys MUST match the English spring_type labels emitted at ingest
# (scripts/ingest.py SPA_QUALITY_MAP values) so a future recommend-time lookup
# on OnsenResult.spring_type lines up exactly.
SPRING_BENEFITS: dict[str, str] = {
    "Simple Spring": "...", "Bicarbonate Spring": "...", "Chloride Spring": "...",
    "Sulfate Spring": "...", "Iron Spring": "...", "Sulfur Spring": "...",
    "Acidic Spring": "...", "Radon Spring": "...", "Iodine Spring": "...",
    "Carbon Dioxide Spring": "...", "Aluminium Spring": "...",
    "Copper-Iron Spring": "...", "Other": "...",
}
def benefits_for(spring_type: str | None) -> str | None: ...
```
Keys are copied verbatim from `scripts/ingest.py:34-48` `SPA_QUALITY_MAP` values —
that is the contract that makes the future `recommend`-time lookup (and the
`spring_types.md` content) line up. Authored now alongside the KB; consumed by
`recommend` later (and optionally injected into the `ask` answer when the question
is spring-type-specific). A focused unit test asserts every `SPA_QUALITY_MAP`
value has a `SPRING_BENEFITS` entry (drift guard).

**Tests that will break:** none; new module + new test.

---

## Step E — KB ingest path — small PR

**Decision: a new ingest script, not extending `scripts/ingest.py`.** That script
is tightly coupled to onsen JSONL (`parse_location`, `SPA_QUALITY_MAP`,
`is_translated` keyed on `name_en`, `detail_url` as id, sibling-JSONL write-back).
Prose markdown has none of that shape. A separate `scripts/ingest_knowledge.py`
keeps each ingester single-purpose (matching the existing `ingest.py` /
`ingest_regions.py` split) and avoids regressing the live onsen ingest.

`scripts/ingest_knowledge.py` responsibilities:
1. Read every `*.md` under `settings.kb_data_dir`; parse front-matter (`doc_type`,
   `source_lang`, `source_ja`, `source_url`).
2. **Translate-at-ingest:** if `source_lang != "en"`, translate the prose JA→EN by
   reusing the `translate_batch` pattern from `ingest.py:70-94` (gpt-4o-mini, temp
   0). For `source_lang="en"` docs this is a pass-through. Always embed the
   **English**.
3. **Chunk** each doc by `##` heading, then size-cap. Prose chunking: target
   ~500–800 chars per chunk with ~80–100 char overlap, never splitting
   mid-sentence; one heading section = one chunk when it fits, else split on
   paragraph boundaries. Small docs may be a single chunk (acceptable while tiny —
   prompt-stuffing is fine until the KB outgrows cheap context).
4. **Metadata per chunk:** `doc_type`, `source_filename` (the `.md` name),
   `heading`, `source_lang`, `source_ja` (Chroma rejects `None` — store `""` when
   absent, mirroring `build_metadata` at `ingest.py:202-215`), `chunk_index`.
5. **Embed the English chunk text**; `collection.upsert(ids=[f"{filename}#{chunk_index}"],
   documents=[english_text], metadatas=[meta])` into `get_kb_collection()`.
   Deterministic ids → re-ingest is idempotent (upsert), like the onsen path.

**Tests that will break:** none existing. New `test_ingest_knowledge.py` (chunking
sizes, metadata fields incl. `source_ja`/`source_lang`, idempotent upsert ids,
JA→EN translate path mocked) — mirror `test_ingest.py`.

---

## Step F — the `ask` branch (`answer_question` node) + pipeline wiring — small PR

Add `backend/agent/workflow/ask.py`, mirroring `analyze.py`'s shape (module-level
`ChatOpenAI` built once, strict-grounding `_INSTRUCTIONS`, `run_config` with
`run_name="answer-question"` + tags + metadata, `callbacks` threaded for usage
capture):

```python
async def answer_question(query: str, callbacks: list | None = None) -> str:
    """Grounded ask-mode answer over the Layer 2 KB. Returns a reply string."""
    records = query_knowledge(query, settings.ask_top_k, settings.ask_max_distance)
    if not records:
        return _NO_INFO_REPLY          # explicit "I don't have that information"
    answer = await _llm.ainvoke([...grounded prompt with the chunks...], config=run_config)
    return answer
```

- Retrieval: `query_knowledge` (Step C) with `ask_top_k` and `ask_max_distance`.
  Empty/weak retrieval → the explicit no-info fallback **without** an LLM call
  (cheap, deterministic, no fabrication risk).
- The model is `ChatOpenAI(model=settings.ask_model or settings.intent_model, ...)`.
  Plain string output (not structured) — the answer is the `reply`.
- Grounding prompt (sketch in Step G).

**Wire into `pipeline.py`** — replace the stub block at `pipeline.py:236-244`:
```python
if intent.mode == "ask":
    if settings.ask_enabled:
        reply = await answer_question(intent.query, callbacks=callbacks)
    else:
        reply = _ASK_STUB_REPLY        # keep the safe stub when gate is off
    onsens, hotels = [], []
    _log_cost(session_id, intent.mode, usage_cb, started)
    save_message(session_id, message, reply)
    return AgentResponse(reply=reply, onsens=onsens, hotels=hotels,
                         recommendation=None).model_dump()
```
This keeps the gate pattern identical to `analyze_enabled` (`pipeline.py:254`):
off by default → unchanged behavior (the stub); flipped on → real RAG.
`_ASK_STUB_REPLY` stays for the gated-off path. Cost logging (`_log_cost`) and
history save are preserved, so LangSmith cost-by-mode and conversation context
keep working. The `usage_cb` already spans this call (`pipeline.py:223` comment
says "intent + analyze" — extend the comment to include ask).

**Tests that will break:**
- `eval_flow.py` `structure()` (`eval_flow.py:377-378`) asserts `reply ==
  _ask_stub_reply()`. When `ask_enabled=True` the reply is a real answer. Update
  the ask branch of `structure()` to assert `not onsens and recommendation is
  None and reply` is non-empty (and, when gated on, NOT the stub). See Step H.
- `test_workflow_branching.py` / `test_intent_modes.py` likely assert the ask stub
  reply — update to gate-aware expectations (stub when `ask_enabled=False`, real
  answer when patched on).

---

## Step G — grounding prompt contract

Mirror `analyze.py`'s strict-grounding `_INSTRUCTIONS` (`analyze.py:66-80`).
Contract:

- **Input:** the user's question (`intent.query`) + the retrieved KB chunks, each
  rendered with its `doc_type`/`heading` so the model can attribute. Optionally
  append `benefits_for(spring_type)` when the question is spring-type-specific.
- **Output:** a concise English answer derived **ONLY** from the provided chunks.
- **Rules (verbatim-strict, no fabrication):** "Answer ONLY from the provided
  knowledge passages. Do NOT use outside knowledge. If the passages do not contain
  the answer, reply exactly: *I don't have that information yet — I can help with
  onsen etiquette, bathing steps, spring-type benefits, tattoo guidance, and trip
  prep.* Do NOT state per-onsen tattoo policies, prices, or hours — those are not
  in this knowledge base." (The last clause protects the high-stakes-field caveat
  from the V2.5 plan, `V2_IMPLEMENTATION_PLAN.md:233-236`.)
- The deterministic no-retrieval fallback in Step F means the model is never asked
  to answer from nothing — belt and suspenders for the "I don't know" path so the
  eval's grounding stays green.

---

## Step H — wire the existing `ask` eval example — small PR

`scripts/eval_flow.py` already has the ask example (`eval_flow.py:162-168`) and an
`ask` cost/latency budget (`eval_flow.py:78,84`). Two changes:
1. `structure()` ask branch (`eval_flow.py:377-378`): stop asserting `reply ==
   _ask_stub_reply()`; assert `not onsens and (outputs.get("recommendation") is
   None) and bool(reply)`. When the harness runs with `ask_enabled` flipped on
   (mirror the `analyze_enabled` flip at `eval_flow.py:456-457,476-477` — add an
   `ask_enabled` flip in the same try/finally), additionally assert `reply !=
   _ask_stub_reply()`.
2. Optionally add a **grounding/no-fabrication evaluator for ask**: an LLM-judge
   (or a keyword-containment check against the KB chunk corpus) scoring that the
   answer's claims are supported by retrievable KB content, plus a dedicated
   "no-data → I don't know" ask example (a question outside the KB, e.g. "what's
   the wifi password at the onsen?") asserting the answer is the no-info fallback.
   This is the analogue of the onsen `grounding` evaluator (`eval_flow.py:327-353`)
   for prose.

`test_eval_flow.py` will need its `structure()`-ask expectation updated to match.

---

## Rollout

Mirrors the `analyze_enabled` rollout (`V2_IMPLEMENTATION_PLAN.md`):
1. Land Steps A–H behind `ask_enabled=False` (default). Prod behavior unchanged —
   ask still returns the stub. Dead/safe until flipped.
2. **Ingest the KB** into the new collection: run `scripts/ingest_knowledge.py`
   locally (writes to `settings.chroma_path` via `get_kb_collection()`), and **on
   Railway re-run the ingest gate** so the `onsen_knowledge` collection exists in
   the prod Chroma volume. **Railway re-ingest gate consideration:** the prod
   Chroma volume is populated by the deploy-time ingest; adding a second
   collection means the ingest gate must also invoke `ingest_knowledge.py` (and
   `KB_DATA_PATH=/app/data/knowledge` must be set, or the `kb_data_dir` default
   resolves it). Without this, flipping `ASK_ENABLED=true` against a Chroma volume
   that has no `onsen_knowledge` collection yields an empty collection → every ask
   hits the "I don't know" fallback. So: **ingest first, flip second.**
3. Staging: set `ASK_ENABLED=true`, fire the eval (`eval_flow.py`) and a manual ask
   smoke; confirm grounding + the no-info fallback + cost within the `ask: 0.01`
   budget.
4. Cutover: `ASK_ENABLED=true` in Railway env. Keep the stub path one release for
   rollback (just flip the env back).

---

## Open risks

1. **KB too small for vectors.** With seven short docs, a vector collection may be
   over-engineering early on; prompt-stuffing the whole corpus is cheaper and
   simpler. Mitigation: the `ask_top_k`/`ask_max_distance` knobs and the small
   corpus mean retrieval degenerates gracefully; graduate from stuffing→vectors is
   already the chosen shape, so this is a non-blocker. Record it as a known caveat.
2. **Translation quality on domain terms.** JA→EN of onsen jargon (rotenburo,
   kakeyu, kaiseki) can mistranslate. Mitigation: author docs in English directly
   (`source_lang="en"`) so the translate step is a no-op for V1 of the KB; keep
   `source_ja` only where a JA source phrase is genuinely worth citing.
3. **Empty/weak retrieval handling.** A real question whose answer isn't in the KB
   must hit the "I don't know" path, not a fabricated answer. Mitigation: the
   deterministic no-retrieval short-circuit in Step F + the strict grounding
   prompt + the distance threshold; covered by a dedicated eval example (Step H).
4. **Chunking prose.** Over-chunking fragments etiquette steps; under-chunking
   returns walls of text. Mitigation: heading-aware chunking with overlap (Step E),
   tuned via the eval's ask grounding.
5. **Railway re-ingest gate (operational).** Forgetting to ingest the KB before
   flipping `ASK_ENABLED` → all asks fall back to "I don't know" silently.
   Mitigation: ingest-first/flip-second runbook (Rollout step 2) and a startup log
   line counting `onsen_knowledge` chunks.
6. **`build_ground_truth` cross-contamination.** Only safe because the collection
   is separate (Step C decision). If anyone later collapses to one collection +
   `doc_type` filter, `eval_flow.py:108-121` would ingest KB chunks as onsen names
   — call this out in the `store.py` docstring.

---

## Critical files

- `backend/agent/workflow/pipeline.py` — replace the ask stub (lines 236-244); add `answer_question` wiring behind `ask_enabled`.
- `backend/services/retrieval/retrieval_service.py` — add `query_knowledge`, LangChain-agnostic.
- `backend/vectorstore/store.py` — parameterize `get_collection`, add `get_kb_collection`.
- `backend/core/config.py` — add `kb_collection`, `kb_data_path`/`kb_data_dir`, `ask_enabled`, `ask_top_k`, `ask_max_distance`, `ask_model`.
- `backend/scripts/eval_flow.py` — update the ask `structure()` assertion (lines 377-378); flip `ask_enabled` in run_evaluation; optional ask grounding evaluator.

New files: `backend/agent/workflow/ask.py`, `backend/agent/workflow/spring_benefits.py`,
`backend/scripts/ingest_knowledge.py`, `backend/data/knowledge/*.md`.
