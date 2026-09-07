# Delivery Log

Project-facing delivery documentation — mirrors the structure used on a real
EY/Hannover Delivery Lead engagement (see `AI Engineering / Business Language
Training` in Apple Notes for the full framework writeup). Career-facing
reflections and skills evidence live in that Apple Note instead; this file is
for the project itself: scope, readiness, decisions, risks, milestones.

---

## Context & Scope

Solo project — Steve acts as Engineer + BA + Delivery Lead. No external
budget beyond personal pay-as-you-go API costs. Constraints: portfolio/
interview timeline, "no fabrication" architecture rule (see root `CLAUDE.md`),
Google API billing decisions are explicit STOP points requiring Steve's
sign-off before enabling a new SKU.

---

## Readiness Table

| Item | Technical Confidence | Business Dependency | Rework Risk |
|---|---|---|---|
| Trip-mode stop selection by rating | High (mechanical sort, no new API) | Low (data already ingested) | Low |
| Recommend-mode grounding rules (review_summary + rating) | High (pattern proven by trip's existing grounding contract) | Low | Medium (changes live `/chat` output tone/behavior — needs wording sign-off) |

---

## RAID Log

**Risk:** Only 3/10 regions (`hokuriku`, `okinawa`, `tokai`) are ingested with Places data — rating-based selection/grounding only benefits those regions until the rest are ingested.
**Assumption:** `review_summary` (Gemini-generated) is accurate enough to ground pros/cons when attributed as "reviewers mention..." rather than stated as flat fact.
**Issue:** None currently open.
**Dependency:** Recommend-mode grounding-rule wording change depends on Steve's review before implementation (affects live user-facing output).

---

## Key Decisions Log

### Key Decision 1 — Split Places-data grounding by mode (2026-09-06)

**Decision:** Trip mode uses `rating` to select which onsen becomes each region's stop (sort the candidate pool before slicing `pool[:want]` in `agent/trip/itinerary.py::build_itinerary`). Recommend mode uses `review_summary` (attributed — "reviewers mention...") in pros/cons grounding, and `rating` only as a comparison signal in the recommendation paragraph, never as a standalone pro/con bullet.

**Rationale:**
- Trip's stop selection currently has zero quality weighting (`pool[:want]` just takes retrieval order) — rating closes a real, low-risk gap.
- Route *ordering* is a separate, already-solved problem (haversine nearest-neighbour, PR7) — rating affects stop *selection* only, never stop *order*.
- The bare rating number is a popularity score, not an intrinsic fact about the onsen — doesn't belong as a pros/cons bullet, but is legitimate as a recommendation-level comparison ("X is the more highly-rated option").
- `review_summary` text is genuine visitor-experience content (baths, service, crowds) — richer grounding material than today's thin `spring_type`/`location` fields — but it's Google's AI synthesis of other people's opinions, not a verified fact, so it must be attributed rather than stated as flat truth (consistent with the project's anti-fabrication stance).

**Status:** Trip-mode change — **Done** (`agent/trip/itinerary.py::_rank_by_rating`, `services/retrieval/retrieval_service.py` now surfaces `rating`; 3 new tests, full suite 568 passed). Recommend-mode grounding-rule wording — **Done** (`agent/grounding.py`: `review_summary` added to `project_candidates`/`STRICT_GROUNDING_RULES`, `rating`/`user_rating_count` deliberately kept OUT of the LLM prompt entirely — pure display fields the model never sees; attribution requirement reinforced at both the system-prompt and `OnsenAnalysis` schema-field level. Live-tested: pros/cons now ground in real review content, ~50% of review-sourced bullets carry the "Reviewers mention..." attribution phrasing consistently, a residual minority don't — accepted as good-enough per Steve's call (2026-09-07), see Known Gaps below). Full suite: 571 passed.

**Known Gaps (accepted, not blocking):**
- Attribution phrasing ("Reviewers mention...") isn't 100% consistent on review-sourced pros/cons — a framing/honesty nuance (implied confidence, not a factual error: nothing is invented, some bullets just don't signal "this is secondhand opinion, not verified fact"). Chasing full compliance would need a validation/rewrite pass — not worth it for a stylistic gap. Revisit if it ever produces a genuinely misleading bullet in practice.
- Only 3/10 regions have review_summary data, so all of this only sourced from ONE Google-derived review synthesis per onsen — no cross-source calibration. **Future idea (Steve, 2026-09-07): bring in additional review sources for calibration** — would both (a) let the LLM-judge groundedness evaluator (`proscons_grounding`, parked — see Next Key Milestones) check the pros/cons against more than one source, and (b) surface cases where Google's single AI-synthesized summary disagrees with other reviews, which the attribution wording alone can't catch.

---

## Next Key Milestones

**Now:** Local `/chat` smoke test of the full recommend-mode pipeline end-to-end.
**Next:** Ingest remaining 7 regions so rating-based selection/grounding covers the whole dataset.
**Later:** Bring in additional review sources for calibration (cross-check Google's single AI-summary against other sources); re-enable `proscons_grounding` LLM-judge evaluator once recommend grounding uses real ratings.
