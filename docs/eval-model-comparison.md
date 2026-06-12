# Model comparison — `analyze_onsen` (recommend brain): gpt-4o vs gpt-4o-mini

**Question:** Is `gpt-4o-mini` a safe, cheaper drop-in for the V2.5 recommend
brain (`analyze_onsen`) versus the current default `gpt-4o`? This is the hard-
numbers backing for the README's model-swap story.

**TL;DR:** On the measurable axes — **cost, latency, and the structural
contract** — `gpt-4o-mini` is strictly better: **~14× cheaper** on recommend
calls, **~1.8× faster** on recommend latency, and it passes **every** evaluator
(7/7, including the latency budget that `gpt-4o` blew on the heavy
recommend+hotels example). Grounding and structure are identical across models —
as expected, since neither is decided by the analyze model. **The one thing this
eval does NOT measure is the *quality* of the pros/cons + recommendation
judgement.** A spot-check of two recommend outputs shows mini's reasoning holds
up (same onsen chosen, grounded rationale), but a real go/no-go needs an
LLM-as-judge evaluator. Recommendation: **mini is viable on cost/structure;
ship it behind an LLM-judge quality gate before making it the default.**

---

## Setup

- **Harness:** `backend/scripts/eval_flow.py` — a LangSmith experiment over the
  real `run_workflow` (no mocks). Dataset `onsen-flow-evals`, 7 examples across
  search / recommend / recommend+hotels / ask / no-data. The harness forces
  `analyze_enabled=True`, so recommend examples genuinely exercise the analyze
  brain.
- **Evaluators (4):** `grounding` (onsen NAME grounding vs ChromaDB truth),
  `structure` (per-mode shape contract), `cost_budget` (per-mode $ ceiling),
  `latency` (per-mode ms ceiling). Each captures per-run `_cost_usd` and
  `_latency_ms`.
- **What was swapped:** only the analyze model, via the `ANALYZE_MODEL` env var.
  The intent step stays on `gpt-4o-mini` (`intent_model`) in both runs — only
  the recommend judgement model changes. Two runs total (each makes paid OpenAI
  calls):
  1. `ANALYZE_MODEL=gpt-4o .venv/bin/python scripts/eval_flow.py`
  2. `ANALYZE_MODEL=gpt-4o-mini .venv/bin/python scripts/eval_flow.py`
- **Harness tweak (this branch):** the experiment name and metadata now embed
  the analyze model (`experiment_prefix=f"onsen-flow-analyze-{settings.analyze_model}"`)
  so the two runs are distinguishable in LangSmith. No evaluator/behaviour
  change; the full backend suite stays green (211 passed).

### Experiments

| Model | Experiment | LangSmith |
|---|---|---|
| `gpt-4o` | `onsen-flow-analyze-gpt-4o-b321f8e6` | [compare view](https://apac.smith.langchain.com/o/ab36105e-d03d-42c3-b9fd-683cc44f4f5f/datasets/4eca23d5-8f77-4ffa-89ed-a16abbc9f655/compare?selectedSessions=4eecac46-d8ba-4fda-b722-9693410b82e4) |
| `gpt-4o-mini` | `onsen-flow-analyze-gpt-4o-mini-0d03df84` | [compare view](https://apac.smith.langchain.com/o/ab36105e-d03d-42c3-b9fd-683cc44f4f5f/datasets/4eca23d5-8f77-4ffa-89ed-a16abbc9f655/compare?selectedSessions=061355e7-361e-49b5-a395-22aaafa1a693) |

---

## Results

### Per-evaluator pass rate (7 examples)

| Evaluator | gpt-4o | gpt-4o-mini |
|---|---|---|
| grounding | 7/7 | 7/7 |
| structure | 7/7 | 7/7 |
| cost_budget | 7/7 | 7/7 |
| latency | **6/7** | **7/7** |

The single `gpt-4o` latency failure is the **recommend+hotels Shizuoka** example
(20 candidate onsen + a Rakuten hotel lookup): **28.1 s**, over its 20 s budget.
`gpt-4o-mini` ran the same example in **12.7 s** — comfortably inside budget.

### Cost (USD per run)

| Scope | gpt-4o | gpt-4o-mini | Δ (4o ÷ mini) |
|---|---|---|---|
| Mean cost, all 7 examples | $0.001972 | $0.000210 | 9.4× |
| **Mean cost, recommend-only (2)** | **$0.006645** | **$0.000477** | **13.9×** |
| Total experiment cost | $0.013806 | $0.001469 | 9.4× |

### Latency (ms per run)

| Scope | gpt-4o | gpt-4o-mini | Δ (4o ÷ mini) |
|---|---|---|---|
| Mean latency, all 7 examples | 6,459 ms | 4,372 ms | 1.48× |
| **Mean latency, recommend-only (2)** | **16,568 ms** | **9,183 ms** | **1.80×** |

### Per-example detail

| Mode | Message | 4o cost | mini cost | 4o latency | mini latency |
|---|---|---:|---:|---:|---:|
| search | Find onsen in Okinawa | $0.000103 | $0.000104 | 2,141 ms | 2,910 ms |
| search | Find onsen in Shizuoka | $0.000103 | $0.000103 | 1,744 ms | 2,213 ms |
| **recommend** | Recommend an onsen in Okinawa (quiet soak) | **$0.003429** | **$0.000300** | 5,076 ms | 5,617 ms |
| **recommend** | Recommend onsen in Shizuoka + nearby hotels | **$0.009861** | **$0.000653** | **28,060 ms** | **12,748 ms** |
| ask | Onsen etiquette with tattoos | $0.000105 | $0.000105 | 2,406 ms | 1,946 ms |
| no-data | Find onsen in Hokkaido | $0.000102 | $0.000103 | 2,454 ms | 1,683 ms |
| no-data | Find onsen in Tokyo | $0.000102 | $0.000101 | 3,334 ms | 3,490 ms |

> Note the search / ask / no-data rows are **~$0.0001 in both runs** — those
> modes never invoke the analyze brain (only the intent model fires, and that's
> fixed at `gpt-4o-mini` in both runs). The entire cost/latency delta lives in
> the two recommend rows, which is exactly where the analyze model is exercised.

---

## Honest interpretation — what this eval can and cannot measure

**Grounding being identical (7/7 vs 7/7) is EXPECTED, not a null result.** The
`grounding` evaluator checks onsen *name* grounding, and which onsen are returned
is decided by **deterministic ChromaDB retrieval**, not by the analyze model. The
analyze model only attaches pros/cons and writes the recommendation paragraph —
it cannot change the set of names. So both models will always score identically
on grounding here, by construction.

**`structure` is a coarse contract, and mini clears it.** For recommend it only
requires `recommendation` to be non-null and ≥1 onsen to carry non-empty pros.
That's a shape check, not a quality check — mini passing it tells us the output
is *well-formed*, not that it's *good*.

**So the meaningful MEASURED deltas are COST, LATENCY, and "does mini still
satisfy the contract?"** — and on all three mini wins clearly: ~14× cheaper on
recommend, ~1.8× faster on recommend, and it actually *improves* the latency
pass rate (it kept the heavy recommend+hotels example inside budget where
`gpt-4o` did not).

**What is NOT captured: the QUALITY of the judgement.** The whole reason
`analyze_onsen` earns an LLM call is *judgement* — are the pros/cons genuinely
grounded in the description (vs plausible-but-invented), and does the
recommendation paragraph actually reflect the user's stated preference? None of
the four current evaluators score that. There is already a standing
`TODO(LLM-judge)` in `eval_flow.py` for pros/cons groundedness; that judge is the
correct instrument for a quality go/no-go, and it does not exist yet. **Until it
does, this comparison proves mini is cheaper, faster, and structurally valid —
not that it is qualitatively as good.**

### Qualitative spot-check (eyeballed, not scored)

Recommend example *"Recommend an onsen in Okinawa for a couple wanting a quiet
relaxing soak"* (3 candidates), recommendation paragraph from each model:

- **gpt-4o:** *"Yamada Onsen is the best fit for a quiet, relaxing onsen for
  couples. It offers beautiful views of the East China Sea … and includes
  massage services … Unlike the others, it's not primarily city-focused, which
  may contribute to a quieter environment."*
- **gpt-4o-mini:** *"For a quiet and relaxing soak for couples, Yamada Onsen
  stands out due to its beautiful sea views and additional massage services,
  promoting both relaxation and a peaceful atmosphere away from the city
  bustle."*

Both models **picked the same onsen** (Yamada Onsen) for the same grounded
reasons (sea views, massage, away from the city), and both tied "city-located"
candidates to a quietness con. Mini actually produced cons for **all three**
candidates (gpt-4o left one with none). On this sample mini's judgement is
coherent and grounded — encouraging, but n=1 and unscored. Do not generalise
from it; that's the LLM-judge's job.

---

## Recommendation

**`gpt-4o-mini` is viable on cost and structure, and is the likely winner — but
gate the switch on an LLM-judge quality evaluator before flipping the default.**

Concretely:

1. **Keep `analyze_model` default at `gpt-4o` for now** (no behaviour change),
   since the quality axis is unmeasured.
2. **Build the LLM-judge evaluator** (the existing `TODO(LLM-judge)` in
   `eval_flow.py`): score pros/cons groundedness against the candidate
   description, and recommendation relevance against the stated preference. Run
   it across both models.
3. **If mini's judge scores are at parity with gpt-4o**, switch the default to
   `gpt-4o-mini` via `ANALYZE_MODEL` — it's ~14× cheaper per recommend, ~1.8×
   faster, and it fixed the recommend+hotels latency-budget miss. That's a large,
   measured win with no structural regression.

The swap is already a one-line env change (`ANALYZE_MODEL=gpt-4o-mini`) thanks to
the `analyze_model` knob — the model layer is genuinely swappable, which is the
README's model-swap claim this document evidences.
