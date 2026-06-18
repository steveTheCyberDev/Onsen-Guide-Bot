# Onsen Eval — What We Judge

A reference for the eval harness in `backend/scripts/eval_flow.py`: what each evaluator
actually inspects, against which source of truth, and how a pass/fail is decided. Keep this
in sync when adding or changing evaluators.

## The model

The **target** (`make_target_with_usage()`) runs the real `run_workflow()` on each example
and returns the `AgentResponse` plus `_cost_usd` / `_latency_ms`. It judges nothing. The
**evaluators** then each inspect one part of that output and score it `1` (pass) / `0`
(fail) / `None` (abstain — not applicable / no signal).

```
                    TARGET output (AgentResponse + _cost_usd + _latency_ms)
                    +---------------------------------------------------+
                    |  onsens[].name        -> grounding                |
                    |  onsens[].pros/cons   -> proscons_grounding (judge)|
                    |  reply (ask answer)   -> ask_grounding (judge)     |
                    |  recommendation+shape -> structure                |
                    |  _cost_usd            -> cost_budget              |
                    |  _latency_ms          -> latency                  |
                    +---------------------------------------------------+
```

## What is judged against what

| Evaluator | What it judges (the claim) | Reads field | Source of truth | Type | Pass = |
|---|---|---|---|---|---|
| **grounding** | Are the onsen **names** real? | `onsens[].name` | **ChromaDB** allowed names for the prefecture | deterministic | every name in DB (or empty when no-data) |
| **proscons_grounding** | Are the **pros/cons** invented? | `onsens[].pros`/`cons` | **that onsen's own facts** (name/location/spring_type/description) | LLM judge | every onsen's pros/cons supported by its facts |
| **ask_grounding** | Is the **ask answer** made up? | `reply` | **the KB chunks re-retrieved** for the question | LLM judge | every claim in the answer supported by the chunks |
| **structure** | Did the **right mode** run / right fields filled? | whole-response shape | the example's `expected_mode` | deterministic | fields match the mode's contract |
| **cost_budget** | Too expensive? | `_cost_usd` | per-mode `$` budget | deterministic | cost <= budget |
| **latency** | Too slow? | `_latency_ms` | per-mode `ms` budget | deterministic | time <= budget |

## The three "grounding" siblings — different artifact, different source

```
grounding           ->  the NAMES       ->  vs the DATABASE        (did we invent a place?)
proscons_grounding  ->  the PROS/CONS   ->  vs that onsen's FACTS  (did we invent qualities?)
ask_grounding       ->  the ASK ANSWER  ->  vs the RETRIEVED DOCS  (did we invent an answer?)
```

- **`grounding`** = fact-existence. A **name** has an exact DB match, so it's deterministic
  and cheap. "Fuji Dream Spa" not in the prefecture's set -> fabricated -> fail.
- **`proscons_grounding`** / **`ask_grounding`** = faithfulness of **free text**. Prose has
  no DB row to match, so it needs an LLM judge that decides whether the generated text is
  supported by its source.

### How `grounding` knows a name is/ isn't in ChromaDB
ChromaDB is read **once** at the start (`build_ground_truth()` -> one `collection.get()`),
folded into `prefecture -> set(normalized names)`, and cached in module-level
`_GROUND_TRUTH` via `set_ground_truth()`. Each check is then a pure in-memory set lookup
(`normalize(name) not in allowed`) — no per-example DB call. Match is coarse (lowercase /
trim / collapse whitespace); romanization and "Onsen"-suffix variants are a known
"tighten later" simplification.

### How `ask_grounding` decides pass/fail
1. Not ask mode -> abstain (`None`).
2. `reply` empty / == stub (gate off) / == no-info fallback (a correct refusal) -> abstain.
3. Recover the question (from `inputs.message` / `example.inputs.message`); none -> abstain.
4. **Re-retrieve** KB chunks for the question (same retrieval the ask node uses). Real
   answer but **no chunks** -> score `0` (unsupported by definition).
5. Judge prompt = `PASSAGES: <chunks>` + `ANSWER: <reply>` -> `_llm_judge` -> GROUNDED `1` /
   UNGROUNDED `0` / unrecognised|error `None`.

It measures **faithfulness** (answer backed by sources), **not relevance** (does it address
the question) or **correctness** (are the sources right). A faithful summary of the chunks
passes even if it's a weak answer. It also re-runs retrieval rather than capturing the
exact chunks the node used — fine while retrieval is deterministic; worth flagging if that
changes.

## Which evaluators apply, by mode

| Mode | grounding | proscons | ask_grnd | structure | cost | latency |
|---|---|---|---|---|---|---|
| **search** | yes | – | – | yes | yes | yes |
| **recommend** | yes | yes | – | yes | yes | yes |
| **ask** | yes* | – | yes | yes | yes | yes |
| **no-data** | yes | – | – | yes | yes | yes |

`–` = abstains (`None`, rendered `-`). `*` ask has no onsens, so grounding just confirms
emptiness.

## What `structure` checks (per mode)

| Mode | structure passes iff |
|---|---|
| **recommend** | `recommendation` not None AND >=1 onsen has non-empty `pros` |
| **search** | `recommendation` is None AND every onsen has empty `pros` & `cons` |
| **ask** | `onsens` empty AND `recommendation` None AND `reply` non-empty (when `ask_enabled`: `reply` != stub; if `expect_no_info`: `reply` == exact no-info fallback) |
| **no-data** | `onsens` empty |

`structure` is deterministic and **never abstains** — it always votes `0`/`1` for every
example, and any `0` counts toward the non-zero exit code. A fully green (CI-gateable) run
requires `structure` = pass everywhere. Only the LLM-judge evaluators may abstain.

## Pass/fail semantics
- `1` = pass, `0` = fail, `None` = abstain (skipped: not counted, never a failure, shown
  as `-`).
- A broken/garbage judge returns `None` (abstain), **never a false pass** — the
  deterministic `grounding` check remains the hard guard.
- `run_evaluation()` returns the count of failing `(example, evaluator)` pairs as the exit
  code (0 = all pass), so it can gate CI.
