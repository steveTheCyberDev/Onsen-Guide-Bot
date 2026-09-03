# Next Steps — Post-V3-Launch Backlog

> Written 2026-08-29, the day `trip_enabled=true` went live in prod and was verified end-to-end.
> This is a planning doc, not a locked roadmap — items are grouped by what they depend on, not by
> priority order, so it's easy to re-sort once you've read through the current architecture yourself.

---

## Track A — code-only, no new external dependency

These need no billing decision and no new API integration. They build on what's already shipped.

### A1. Multiple candidate itineraries (not just one naive plan)
**Where:** `agent/trip/itinerary.py` (the `plan` node), `agent/trip/analyze.py` (the recommendation step).
**Why it matters:** the trip-planner currently produces exactly **one** deterministic itinerary. The
"Guide Recommendation" text explains *that* plan, but there's nothing to reason *against* — it can't
say "I picked this over a more relaxed option because..." because no alternative was ever generated.
Generating 2-3 candidates (e.g. varying pace, or which region gets dropped when over-constrained) turns
the existing `analyze` step from "justify the only option" into a genuine judgment call — the same shift
`analyze_onsen` made for `recommend` mode back in V2.5.
**Shape:** the naive assembly in `itinerary.py` is already pure Python/deterministic — generating a
second variant (e.g. swap which outlier region gets dropped in `constraints.py::detect_conflicts`, or a
different nights-per-region split) is a data-generation change, not a new LLM call. The `analyze` step
would then rank/explain across the candidate set instead of a single plan.
**Effort:** medium. No new external dependency, but touches the plan/conflict/analyze nodes together.

### A2. Region ADD-vs-REPLACE ambiguity (found via live user testing, 2026-08-29)
**Where:** `agent/trip/slots.py` (`extract_slots`, `_INSTRUCTIONS`).
**The bug:** mid-conversation, "I prefer Nagano" (narrow the trip to just Nagano) gets extracted
identically to "also add Nagano" (accumulate) — verified by probing the raw LLM call directly, not a
downstream merge bug. The `_INSTRUCTIONS` prompt only ever taught the ADD case explicitly; REPLACE was
never covered.
**Options discussed:**
1. Teach the model explicit REPLACE-language too ("I prefer X", "instead", "actually", "decided") —
   symmetric to how ADD is already handled. Still a guess, just a better-informed one.
2. **(Preferred)** Detect the ambiguity and ask a clarifying question, the same way a missing required
   slot triggers `elicit` today — e.g. *"Would you like Nagano added to your Gifu + Shizuoka trip, or
   would you like to focus only on Nagano?"* Consistent with the project's existing stance of never
   guessing a fact it isn't sure of.
3. Simplify the model entirely: always replace with whatever regions are named in the latest message,
   dropping the incremental multi-turn ADD case. Simpler, but breaks a flow this planner was explicitly
   built to support (spelling out a multi-region trip one region per turn) — only worth it if that flow
   turns out to be rare in real usage.
**Effort:** small (option 1) to medium (option 2 — needs a new signal on `SlotUpdate` + a routing change
in `_route_after_gather`).

### A3. Re-enable the LLM-as-judge pros/cons evaluator
**Where:** `backend/scripts/eval_flow.py` (`proscons_grounding`, `ask_grounding` — built, parked, commented out of `EVALUATORS`).
**Why parked:** false-failed a clean, correctly-grounded release earlier (non-deterministic judge noise
while the flow + data were still moving). Worth revisiting now that the flow has stabilized and the
dataset has grown from 9 → 20 examples with the region gap closed.
**Effort:** small — mostly re-running it against the current system and deciding if the false-negative
rate is now acceptable.

---

## Track B — one Google Places billing decision, unlocks three features

These are three separate feature asks that all resolve to the same infrastructure decision: turning on
Google Places API billing (may reuse the existing Google Maps key/project — needs confirming). This was
already scoped as **PR6** in `docs/v3-trip-planner-plan.md` and has sat deferred behind this exact call
since mid-July.

### B1. Real travel time (upgrade the haversine seam)
**Where:** `services/routing/routing_service.py` — already has a documented seam for a Distance Matrix
backend; currently pure great-circle distance (haversine), which is what powers today's conflict
detection ("3 regions... spread too thin"). Real driving/transit time would make that reasoning more
accurate, especially for mountainous regions where straight-line distance understates travel time.

### B2. Attractions near/around onsen stops
**Where:** new — would sit alongside the existing per-stop hotel lookup in `agent/trip/itinerary.py`,
using Google Places Nearby Search around each onsen's coordinates (already geocoded, already in Chroma
metadata).

### B3. Onsen photos
**Where:** new — Google Places Photos API, keyed off a Place ID looked up per onsen (or per hotel, if
extending the hotel cards too). No photo data exists anywhere in the pipeline today.

### B4. (Also unlocked, was already planned) Real ratings to ground pros/cons
**Where:** the originally-scoped PR6 — `services/places/` + an ingest-time enrichment script, writing
real Google rating/review-count into Chroma metadata. This is what the parked LLM-judge evaluator (A3)
was waiting on — real ratings close the "pros/cons are LLM-inferred, not measured" groundedness gap.

**Effort once billing is decided:** each of B1-B4 is a moderate, mostly-independent slice (new
`services/places/` or extending `services/routing/`), following the same ingest-time-enrichment pattern
already used for onsen coordinates and hotel translation. The billing decision itself — confirming SKUs,
understanding the cost model at expected traffic — is the actual blocker, not the code.

---

## Deferred, lower priority (already known, not re-litigated here)

- **Model migration** — GPT-4o → Claude Sonnet/Opus with a provider fallback chain (`langchain-anthropic`
  already a dependency). Needs a measure-before-flip pass, same discipline as the ReAct→workflow redesign.
- **Railway Postgres + multi-worker** — only matters once traffic actually demands more than one worker;
  the `PostgresSaver` checkpointer path raises `NotImplementedError` until this lands.
- **Weather signal (V3.1)** — `services/weather/` via Open-Meteo (keyless) — would let the currently-honest
  "weather × outdoor × season" conflict example actually get solved, rather than abstaining.
- **Dependabot backlog** — 24 open dependency-bump PRs (#103-126), ranging from safe patch bumps to major
  jumps (React 18→19, Vite 5→8, ChromaDB 0.6→1.5.9). None urgent; worth a dedicated pass, and at least two
  (`vitest`, `jsdom`) directly conflict with a known local-machine pin — test before merging those.
