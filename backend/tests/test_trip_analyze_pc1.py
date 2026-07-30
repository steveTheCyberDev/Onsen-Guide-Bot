"""pc1 tests — grounded analyze/recommendation step in the trip planner.

The trip planner now hangs an additive, ``analyze_enabled``-gated ``analyze`` node
after the plan/check_constraints re-plan loop. It reuses the recommend-brain
pattern (ONE grounded LLM call over a compact projection) to attach per-stop
pros/cons + a top-level recommendation ("why this itinerary suits you"), grounded
ONLY in the assembled onsen stops + the traveller's slots.

Everything external is ALWAYS mocked so the suite is deterministic and free:
  * the analyze LLM — ``agent.trip.analyze._llm`` (returns a canned TripGuideResult);
  * the gather extraction LLM — ``agent.trip.slots._llm``;
  * per-region retrieval — ``agent.trip.itinerary.query_onsen_structured``;
  * hotel lookup — the conftest autouse fixture stubs ``search_hotels`` to [].

These tests assert: pros/cons merge back BY INDEX, the recommendation flows, the
projection excludes coords/urls, the gate-off path is a clean no-op, the grounded
call never invents a stop beyond the assembled itinerary, and — via the graph — the
PR7 conflict prose still SURVIVES (composes) when analyze also runs.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent import grounding
from agent.schemas import OnsenResult
from agent.trip import analyze as analyze_module
from agent.workflow import analyze as recommend_module
from agent.trip import itinerary as itinerary_module
from agent.trip import slots as slots_module
from agent.trip.agent import plan_trip
from agent.trip.analyze import (
    TripGuideResult,
    _OnsenAnalysis,
    _project,
    _trip_context,
    analyze_itinerary,
)
from agent.trip.graph import build_trip_graph
from agent.trip.slots import SlotUpdate


# --- fixtures / helpers ------------------------------------------------------


def _onsen_result(name, **overrides):
    base = dict(
        name=name,
        location="Gero, Gifu",
        spring_type="Sulfur Spring",
        spa_quality=f"{name} is a relaxing sulfur spring with mountain views.",
        lat=35.8,
        lng=137.0,
    )
    base.update(overrides)
    return OnsenResult(**base)


def _mock_llm(result: TripGuideResult) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=result)
    return llm


def _cfg(session_id: str) -> dict:
    return {"configurable": {"thread_id": session_id}}


def _onsen_record(name: str, lat: float = 35.8, lng: float = 137.0, prefecture: str = "Gifu") -> dict:
    return {
        "name": name,
        "location": f"City, {prefecture}",
        "spring_type": "Sulfur Spring",
        "spa_quality": f"{name} is a sulfur spring.",
        "detail_url": f"https://example.com/{name.replace(' ', '-')}",
        "lat": lat,
        "lng": lng,
    }


def _mock_slots_llm(update: SlotUpdate) -> MagicMock:
    llm = MagicMock()
    llm.ainvoke = AsyncMock(return_value=update)
    return llm


def _retrieval_by_region(mapping: dict[str, list[dict]]) -> MagicMock:
    def _fn(query, prefecture=None, n_results=20):
        return list(mapping.get(prefecture, []))

    return MagicMock(side_effect=_fn)


# --- single-source grounding contract (anti-drift guard) ---------------------

# The recommend brain's effective prompt BEFORE the grounding refactor. This test
# is a behaviour-preserving guard: the live recommend (analyze_onsen) prompt must be
# byte-identical to this after the extraction into agent/grounding.py.
_RECOMMEND_INSTRUCTIONS_PRE_REFACTOR = (
    "You are an expert guide for Japanese hot springs (onsen). You are given a "
    "numbered list of candidate onsen (each with name, spring type, location, and "
    "a short description) and the traveller's stated preference. For each onsen, "
    "give a few short pros and cons, and then recommend which one best fits the "
    "preference and why.\n"
    "STRICT GROUNDING RULES — these override any instinct to be more helpful:\n"
    "- Every pro and con MUST be directly supported by the LITERAL text of that "
    "onsen's provided fields (name, spring type, location, description). Do NOT "
    "infer amenities, scenery, baths, views, atmosphere, crowds, or activities "
    "from the onsen's NAME, from its LOCATION, or from general knowledge about "
    "the area or the spring type.\n"
    "- If an onsen's description is 'none provided', you usually cannot ground "
    "any specific pro or con — return EMPTY pros and cons for that onsen rather "
    "than guessing. It is correct and expected for an onsen to have no pros/cons.\n"
    "- Never invent facilities, prices, opening hours, tattoo policies, transport, "
    "baths, views, or any fact not present in the data.\n"
    "- Refer to each onsen by its given index so your analysis can be matched back.\n"
    "- Keep pros/cons short (a few words each).\n"
    "- The recommendation paragraph may compare spring type and location against "
    "the preference, but must not assert any fact absent from the data."
)


def test_recommend_live_prompt_is_byte_identical_after_refactor():
    # Behaviour-preserving: extracting the grounding contract must NOT change the
    # live recommend path's effective prompt.
    assert recommend_module._INSTRUCTIONS == _RECOMMEND_INSTRUCTIONS_PRE_REFACTOR


def test_both_brains_share_one_grounding_constant():
    # Single source of truth: the SAME grounding constant object is embedded in both
    # brains' system prompts, so hardening it in agent/grounding.py hardens both.
    assert grounding.STRICT_GROUNDING_RULES in recommend_module._INSTRUCTIONS
    assert grounding.STRICT_GROUNDING_RULES in analyze_module._INSTRUCTIONS
    # And both reference the exact same shared objects (not divergent copies).
    assert recommend_module._project is grounding.project_candidates
    assert analyze_module._project is grounding.project_candidates
    assert recommend_module._OnsenAnalysis is grounding.OnsenAnalysis
    assert analyze_module._OnsenAnalysis is grounding.OnsenAnalysis


def test_trip_prompt_keeps_stronger_never_introduce_rule():
    # Trip reconciled its grounding bullets to the shared "onsen" wording, but keeps
    # its stronger anti-invention clause as a trip-specific tail rule.
    assert "NEVER introduce an onsen or stop" in analyze_module._INSTRUCTIONS
    # …and it is NOT smuggled into the shared constant (which would change the live
    # recommend prompt).
    assert "NEVER introduce" not in grounding.STRICT_GROUNDING_RULES


# --- analyze_itinerary unit --------------------------------------------------


@pytest.mark.asyncio
async def test_pros_cons_merge_back_by_index():
    # Two stops; analyses intentionally OUT OF ORDER to prove index-keyed merge.
    onsens = [_onsen_result("Gero Onsen"), _onsen_result("Hirayu Onsen")]
    guide = TripGuideResult(
        analyses=[
            _OnsenAnalysis(index=1, pros=["h-pro"], cons=["h-con"]),
            _OnsenAnalysis(index=0, pros=["g-pro"], cons=["g-con"]),
        ],
        recommendation="This itinerary balances two sulfur springs at a relaxed pace.",
    )
    with patch.object(analyze_module, "_llm", _mock_llm(guide)):
        result, recommendation = await analyze_itinerary(
            {"regions": ["Gifu"], "nights": 2, "pace": "relaxed"}, onsens
        )
    assert result[0].pros == ["g-pro"] and result[0].cons == ["g-con"]
    assert result[1].pros == ["h-pro"] and result[1].cons == ["h-con"]
    assert "relaxed pace" in recommendation


@pytest.mark.asyncio
async def test_out_of_range_index_dropped_without_inventing_stop():
    # One real stop but the LLM references index 7 → dropped; no stop is invented,
    # the real stop keeps empty pros/cons.
    onsens = [_onsen_result("Solo Onsen")]
    guide = TripGuideResult(
        analyses=[_OnsenAnalysis(index=7, pros=["ghost"], cons=[])],
        recommendation="A single relaxing stop.",
    )
    with patch.object(analyze_module, "_llm", _mock_llm(guide)):
        result, _ = await analyze_itinerary({"regions": ["Gifu"], "nights": 1}, onsens)
    assert len(result) == 1  # no stop invented
    assert result[0].pros == [] and result[0].cons == []


@pytest.mark.asyncio
async def test_no_stops_skips_llm_and_returns_none():
    llm = _mock_llm(TripGuideResult(analyses=[], recommendation="unused"))
    with patch.object(analyze_module, "_llm", llm):
        result, recommendation = await analyze_itinerary({"regions": ["Gifu"]}, [])
    assert result == []
    assert recommendation is None
    llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_does_not_fabricate_when_llm_returns_no_analyses():
    onsens = [_onsen_result("Quiet Onsen")]
    guide = TripGuideResult(analyses=[], recommendation="Not much to differentiate.")
    with patch.object(analyze_module, "_llm", _mock_llm(guide)):
        result, recommendation = await analyze_itinerary({"regions": ["Gifu"]}, onsens)
    assert result[0].pros == [] and result[0].cons == []
    assert recommendation == "Not much to differentiate."


# --- projection / context grounding ------------------------------------------


def test_projection_excludes_coords_and_urls():
    onsens = [_onsen_result("Coastal", lat=12.345678, lng=98.7654321)]
    projection = _project(onsens)
    assert "12.345678" not in projection
    assert "98.7654321" not in projection
    assert "lat" not in projection.lower()
    assert "http" not in projection.lower()


def test_projection_includes_judgement_fields():
    projection = _project([_onsen_result("Coastal")])
    assert "Coastal" in projection
    assert "Sulfur Spring" in projection
    assert "Gero, Gifu" in projection
    assert "Description:" in projection


def test_trip_context_includes_slot_prefs():
    ctx = _trip_context(
        {
            "regions": ["Gifu", "Nagano"],
            "nights": 4,
            "pace": "packed",
            "spring_or_scenery_prefs": "mountain views and sulfur springs",
        }
    )
    assert "Gifu, Nagano" in ctx
    assert "4" in ctx
    assert "packed" in ctx
    assert "mountain views and sulfur springs" in ctx


def test_trip_context_handles_missing_prefs():
    ctx = _trip_context({"regions": ["Gifu"], "nights": 2})
    assert "none stated" in ctx  # no spring_or_scenery_prefs
    assert "relaxed" in ctx  # pace defaults to relaxed in the projection


# --- end-to-end via the graph / plan_trip ------------------------------------


@pytest.mark.asyncio
async def test_gate_off_is_clean_noop(monkeypatch):
    # analyze_enabled False (default) → analyze node no-ops: no recommendation, no
    # pros/cons, and the analyze LLM is never called.
    monkeypatch.setattr(analyze_module.settings, "analyze_enabled", False)
    full = SlotUpdate(regions=["Gifu"], nights=2, dates_or_season="autumn")
    retrieval = _retrieval_by_region({"Gifu": [_onsen_record("Gero Onsen")]})
    analyze_llm = _mock_llm(
        TripGuideResult(analyses=[], recommendation="should not be used")
    )
    with patch.object(slots_module, "_llm", _mock_slots_llm(full)), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ), patch.object(analyze_module, "_llm", analyze_llm):
        resp = await plan_trip("2 nights in Gifu this autumn", session_id="pc1-off")

    assert resp.recommendation is None
    assert resp.onsens and all(o.pros == [] and o.cons == [] for o in resp.onsens)
    analyze_llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_gate_on_populates_recommendation_and_proscons(monkeypatch):
    monkeypatch.setattr(analyze_module.settings, "analyze_enabled", True)
    full = SlotUpdate(regions=["Gifu"], nights=2, dates_or_season="autumn", pace="relaxed")
    retrieval = _retrieval_by_region(
        {"Gifu": [_onsen_record("Gero Onsen"), _onsen_record("Hirayu Onsen", lat=35.85, lng=137.1)]}
    )
    guide = TripGuideResult(
        analyses=[
            _OnsenAnalysis(index=0, pros=["sulfur spring"], cons=["remote"]),
            _OnsenAnalysis(index=1, pros=["mountain area"], cons=[]),
        ],
        recommendation="Two relaxed sulfur-spring stops in Gifu suit your autumn trip.",
    )
    with patch.object(slots_module, "_llm", _mock_slots_llm(full)), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ), patch.object(analyze_module, "_llm", _mock_llm(guide)):
        resp = await plan_trip("relaxed 2 nights in Gifu this autumn", session_id="pc1-on")

    assert resp.recommendation == "Two relaxed sulfur-spring stops in Gifu suit your autumn trip."
    by_name = {o.name: o for o in resp.onsens}
    assert by_name["Gero Onsen"].pros == ["sulfur spring"]
    assert by_name["Gero Onsen"].cons == ["remote"]
    assert by_name["Hirayu Onsen"].pros == ["mountain area"]
    # No stop invented beyond the two assembled ones.
    assert set(by_name) == {"Gero Onsen", "Hirayu Onsen"}


@pytest.mark.asyncio
async def test_recommendation_stored_in_itinerary_state(monkeypatch):
    monkeypatch.setattr(analyze_module.settings, "analyze_enabled", True)
    graph = build_trip_graph()
    session_id = "pc1-state"
    full = SlotUpdate(regions=["Gifu"], nights=1, dates_or_season="autumn")
    guide = TripGuideResult(
        analyses=[_OnsenAnalysis(index=0, pros=["sulfur"], cons=[])],
        recommendation="A single relaxed sulfur-spring night in Gifu.",
    )
    with patch.object(slots_module, "_llm", _mock_slots_llm(full)), patch.object(
        itinerary_module, "query_onsen_structured",
        _retrieval_by_region({"Gifu": [_onsen_record("Gero Onsen")]}),
    ), patch.object(analyze_module, "_llm", _mock_llm(guide)):
        await graph.ainvoke({"message": "1 night in Gifu this autumn"}, config=_cfg(session_id))

    snap = graph.get_state(_cfg(session_id)).values
    assert snap.get("recommendation") == "A single relaxed sulfur-spring night in Gifu."
    # pros/cons attached onto the checkpointer-safe selected_onsens dict.
    stop = snap["itinerary"]["regions"][0]["onsens"][0]
    assert stop["pros"] == ["sulfur"] and stop["cons"] == []


@pytest.mark.asyncio
async def test_gate_off_clears_prior_checkpointed_recommendation(monkeypatch):
    # Turn 1 (gate ON) checkpoints a recommendation; Turn 2 on the SAME thread with
    # the gate OFF must return recommendation=None — the no-op branch explicitly
    # clears it rather than leaving the stale value in checkpointed state.
    graph = build_trip_graph()
    session_id = "pc1-clear"
    full = SlotUpdate(regions=["Gifu"], nights=1, dates_or_season="autumn")
    retrieval = _retrieval_by_region({"Gifu": [_onsen_record("Gero Onsen")]})
    guide = TripGuideResult(
        analyses=[_OnsenAnalysis(index=0, pros=["sulfur"], cons=[])],
        recommendation="A relaxed night in Gifu.",
    )

    # Turn 1 — analyze enabled → recommendation set + checkpointed.
    monkeypatch.setattr(analyze_module.settings, "analyze_enabled", True)
    with patch.object(slots_module, "_llm", _mock_slots_llm(full)), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ), patch.object(analyze_module, "_llm", _mock_llm(guide)):
        await graph.ainvoke({"message": "1 night in Gifu this autumn"}, config=_cfg(session_id))
    assert graph.get_state(_cfg(session_id)).values.get("recommendation") == "A relaxed night in Gifu."

    # Turn 2 — same session, analyze DISABLED → recommendation cleared to None.
    monkeypatch.setattr(analyze_module.settings, "analyze_enabled", False)
    with patch.object(slots_module, "_llm", _mock_slots_llm(SlotUpdate())), patch.object(
        itinerary_module, "query_onsen_structured", retrieval
    ):
        r2 = await graph.ainvoke({"message": "anything else?"}, config=_cfg(session_id))
    assert r2.get("recommendation") is None
    assert graph.get_state(_cfg(session_id)).values.get("recommendation") is None


@pytest.mark.asyncio
async def test_elicit_turn_does_not_run_analyze(monkeypatch):
    # A missing required slot short-circuits at elicit — analyze is never reached.
    monkeypatch.setattr(analyze_module.settings, "analyze_enabled", True)
    analyze_llm = _mock_llm(TripGuideResult(analyses=[], recommendation="unused"))
    with patch.object(
        slots_module, "_llm", _mock_slots_llm(SlotUpdate(regions=["Gifu"]))
    ), patch.object(analyze_module, "_llm", analyze_llm):
        resp = await plan_trip("onsen trip in Gifu", session_id="pc1-elicit")

    assert resp.recommendation is None
    assert resp.reply.count("?") == 1
    analyze_llm.ainvoke.assert_not_awaited()


@pytest.mark.asyncio
async def test_conflict_prose_survives_and_composes_with_recommendation(monkeypatch):
    # An over-constrained trip (3 dispersed regions, 3 nights) drops the outlier and
    # the deterministic PR7 conflict prose must remain in the reply, WITH the pc1
    # grounded recommendation added additively on top (separate field).
    monkeypatch.setattr(analyze_module.settings, "analyze_enabled", True)
    graph = build_trip_graph()
    session_id = "pc1-conflict"
    full = SlotUpdate(
        regions=["Gifu", "Nagano", "Shizuoka"], nights=3, dates_or_season="autumn", pace="relaxed"
    )
    records = {
        "Gifu": [_onsen_record("Gero Onsen", 35.8, 137.0, "Gifu")],
        "Nagano": [_onsen_record("Nozawa Onsen", 36.6, 138.2, "Nagano")],
        "Shizuoka": [_onsen_record("Atami Onsen", 34.9, 138.4, "Shizuoka")],
    }
    guide = TripGuideResult(
        analyses=[_OnsenAnalysis(index=0, pros=["sulfur"], cons=[])],
        recommendation="Focusing on Gifu and Nagano keeps the relaxed pace realistic.",
    )
    with patch.object(slots_module, "_llm", _mock_slots_llm(full)), patch.object(
        itinerary_module, "query_onsen_structured", _retrieval_by_region(records)
    ), patch.object(analyze_module, "_llm", _mock_llm(guide)):
        r = await graph.ainvoke(
            {"message": "relaxed 3-night trip across Gifu, Nagano and Shizuoka"},
            config=_cfg(session_id),
        )

    snap = graph.get_state(_cfg(session_id)).values
    # Re-plan still dropped the outlier (PR7 behaviour intact).
    dropped = {d["region"] for d in snap.get("dropped_regions", [])}
    assert dropped == {"Shizuoka"}
    # The deterministic conflict prose SURVIVES in the reply (not clobbered).
    low = r["reply"].lower()
    assert "drop" in low and "shizuoka" in low
    # AND the grounded recommendation is added on top as a separate field.
    assert snap.get("recommendation") == (
        "Focusing on Gifu and Nagano keeps the relaxed pace realistic."
    )
