"""Unit tests for the eval_flow harness — pure functions only, NO paid calls.

These exercise the evaluator functions and the ground-truth helper with
synthetic ``AgentResponse``-shaped dicts and a mocked ChromaDB collection. They
do NOT import/run ``run_workflow`` and do NOT touch LangSmith, so the suite stays
free + fast. The live experiment (paid) lives in ``scripts/eval_flow.py``.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from scripts import eval_flow


# --- ground-truth helper ------------------------------------------------------
def test_build_ground_truth_groups_names_by_prefecture():
    """build_ground_truth reads Chroma metadatas and groups normalized names."""
    fake_collection = MagicMock()
    fake_collection.get.return_value = {
        "metadatas": [
            {"prefecture_en": "Okinawa", "name_en": "Yamada Onsen"},
            {"prefecture_en": "Okinawa", "name_en": "Naha Onsen"},
            {"prefecture_en": "Shizuoka", "name_en": "Atami Onsen"},
            {"prefecture_en": "Shizuoka", "name": "Ito Onsen"},  # name fallback
            {"prefecture_en": "Okinawa"},  # no name → skipped
            {"name_en": "Orphan Onsen"},  # no prefecture → skipped
        ]
    }
    with patch.object(eval_flow, "get_collection", return_value=fake_collection):
        allowed = eval_flow.build_ground_truth()

    assert allowed["Okinawa"] == {"yamada onsen", "naha onsen"}
    assert allowed["Shizuoka"] == {"atami onsen", "ito onsen"}
    assert "Hokkaido" not in allowed  # absent prefecture has no entry


def test_reconcile_has_data_uses_chroma_truth():
    """reconcile_has_data flips authored has_data to match the live ground truth."""
    examples = [
        {"message": "a", "prefecture": "Okinawa", "has_data": False, "expected_mode": "search", "wants_hotels": False},
        {"message": "b", "prefecture": "Hokkaido", "has_data": True, "expected_mode": "no-data", "wants_hotels": False},
        {"message": "c", "prefecture": None, "has_data": False, "expected_mode": "ask", "wants_hotels": False},
    ]
    allowed = {"Okinawa": {"yamada onsen"}}
    out = eval_flow.reconcile_has_data(examples, allowed)

    assert out[0]["has_data"] is True  # Okinawa has data
    assert out[1]["has_data"] is False  # Hokkaido has none
    assert out[2]["has_data"] is False  # ask: prefecture None, authored value kept
    # inputs not mutated
    assert examples[0]["has_data"] is False


def test_reconcile_recomputes_trip_no_data_regions():
    """A trip example's no_data_regions is recomputed from live ground truth."""
    examples = [
        {
            "messages": ["plan a trip"],
            "expected_mode": "trip",
            "prefecture": None,
            "has_data": True,
            "wants_hotels": False,
            "regions": ["Gifu", "Hokkaido"],
            "expected_nights": 3,
            "no_data_regions": [],  # authored empty; reconciliation should fix it
        }
    ]
    allowed = {"Gifu": {"gero onsen"}}  # Hokkaido absent → no data
    out = eval_flow.reconcile_has_data(examples, allowed)
    assert out[0]["no_data_regions"] == ["Hokkaido"]
    # input not mutated
    assert examples[0]["no_data_regions"] == []


def test_multifactor_examples_are_wellformed_and_gate_flags_propagate():
    """The 4 PR7 multi-factor examples parse and their gate flags reach reference_outputs.

    Verifies the dataset authoring is well-formed (each is a complete trip thread
    with valid regions/nights) and that _expectation() carries the new expect_*
    gate keys into the reference outputs the PR7 evaluators read.
    """
    multifactor = [
        ex
        for ex in eval_flow._EXAMPLES
        if ex.get("conflict_factors")  # only the PR7 red-baseline examples set this
    ]
    assert len(multifactor) == 4

    valid_regions = {"Gifu", "Nagano", "Shizuoka", "Aichi", "Okinawa"}
    at_least_one_gate = 0
    for ex in multifactor:
        # Structurally a complete trip thread.
        assert ex["expected_mode"] == "trip"
        assert ex["messages"] and isinstance(ex["messages"], list)
        assert ex["expected_nights"]
        assert ex["regions"] and set(ex["regions"]) <= valid_regions

        exp = eval_flow._expectation(ex)
        # Every gate key is present in the expectation (default or set).
        for key in (
            "expect_constraint_conflict_ack",
            "expect_feasibility_flag",
            "expect_tradeoff_explanation",
            "expect_dropped_regions",
            "conflict_factors",
        ):
            assert key in exp
        if any(
            exp[k]
            for k in (
                "expect_constraint_conflict_ack",
                "expect_feasibility_flag",
                "expect_tradeoff_explanation",
                "expect_dropped_regions",
            )
        ):
            at_least_one_gate += 1
    # Every multi-factor example gates at least one PR7 evaluator.
    assert at_least_one_gate == 4


def test_pre_pr7_examples_leave_gate_flags_off():
    """Non-multi-factor examples must NOT set any PR7 gate flag (so evaluators abstain)."""
    for ex in eval_flow._EXAMPLES:
        if ex.get("conflict_factors"):
            continue  # the PR7 examples are allowed to set gates
        exp = eval_flow._expectation(ex)
        assert exp["expect_constraint_conflict_ack"] is False
        assert exp["expect_feasibility_flag"] is False
        assert exp["expect_tradeoff_explanation"] is False
        assert exp["expect_dropped_regions"] == []


def test_example_input_and_key_cover_both_shapes():
    """Single-message and threaded examples map to the right input payload + key."""
    single = {"message": "Find onsen in Gifu"}
    threaded = {"messages": ["plan a trip", "5 nights in Gifu this autumn"]}
    assert eval_flow._example_input(single) == {"message": "Find onsen in Gifu"}
    assert eval_flow._example_input(threaded) == {
        "messages": ["plan a trip", "5 nights in Gifu this autumn"]
    }
    # Keys are stable and distinguish threads from single messages.
    assert eval_flow._example_key({"message": "Find onsen in Gifu"}) == "Find onsen in Gifu"
    assert (
        eval_flow._example_key({"messages": ["a", "b"]}) == "a||b"
    )


# --- grounding evaluator ------------------------------------------------------
@pytest.fixture(autouse=True)
def _ground_truth():
    """Inject a fixed ground-truth snapshot for the grounding evaluator."""
    eval_flow.set_ground_truth(
        {
            "Okinawa": {"yamada onsen", "naha onsen"},
            "Shizuoka": {"atami onsen"},
        }
    )
    yield
    eval_flow.set_ground_truth({})


def _onsen(name, pros=None, cons=None):
    return {
        "name": name,
        "location": "somewhere",
        "spring_type": "Sulfur Spring",
        "spa_quality": "desc",
        "lat": 1.0,
        "lng": 2.0,
        "pros": pros or [],
        "cons": cons or [],
    }


def test_grounding_passes_when_all_names_in_truth():
    outputs = {"onsens": [_onsen("Yamada Onsen"), _onsen("Naha Onsen")]}
    meta = {"prefecture": "Okinawa", "has_data": True}
    assert eval_flow.grounding(outputs=outputs, reference_outputs=meta)["score"] == 1


def test_grounding_fails_on_fabricated_name():
    outputs = {"onsens": [_onsen("Yamada Onsen"), _onsen("Totally Invented Onsen")]}
    meta = {"prefecture": "Okinawa", "has_data": True}
    assert eval_flow.grounding(outputs=outputs, reference_outputs=meta)["score"] == 0


def test_grounding_no_data_must_be_empty():
    """has_data=False with ANY onsen returned is a fabrication → fail."""
    fab = {"onsens": [_onsen("Hokkaido Phantom Onsen")]}
    empty = {"onsens": []}
    meta = {"prefecture": "Hokkaido", "has_data": False}
    assert eval_flow.grounding(outputs=fab, reference_outputs=meta)["score"] == 0
    assert eval_flow.grounding(outputs=empty, reference_outputs=meta)["score"] == 1


def test_grounding_has_data_but_empty_fails():
    outputs = {"onsens": []}
    meta = {"prefecture": "Okinawa", "has_data": True}
    assert eval_flow.grounding(outputs=outputs, reference_outputs=meta)["score"] == 0


# --- structure evaluator ------------------------------------------------------
def test_structure_recommend_good():
    outputs = {
        "onsens": [_onsen("Yamada Onsen", pros=["quiet", "scenic"])],
        "recommendation": "Yamada Onsen is the quietest pick.",
        "reply": "Found 1 onsen in Okinawa.",
    }
    meta = {"expected_mode": "recommend"}
    assert eval_flow.structure(outputs=outputs, reference_outputs=meta)["score"] == 1


def test_structure_recommend_missing_recommendation_fails():
    outputs = {
        "onsens": [_onsen("Yamada Onsen", pros=["quiet"])],
        "recommendation": None,
        "reply": "x",
    }
    assert eval_flow.structure(outputs=outputs, reference_outputs={"expected_mode": "recommend"})["score"] == 0


def test_structure_recommend_no_pros_fails():
    outputs = {
        "onsens": [_onsen("Yamada Onsen")],
        "recommendation": "some rec",
        "reply": "x",
    }
    assert eval_flow.structure(outputs=outputs, reference_outputs={"expected_mode": "recommend"})["score"] == 0


def test_structure_search_good():
    outputs = {
        "onsens": [_onsen("Yamada Onsen"), _onsen("Naha Onsen")],
        "recommendation": None,
        "reply": "Found 2 onsen in Okinawa.",
    }
    assert eval_flow.structure(outputs=outputs, reference_outputs={"expected_mode": "search"})["score"] == 1


def test_structure_search_with_proscons_fails():
    """Search mode must NOT carry pros/cons (that would be recommend leakage)."""
    outputs = {
        "onsens": [_onsen("Yamada Onsen", pros=["leaked"])],
        "recommendation": None,
        "reply": "x",
    }
    assert eval_flow.structure(outputs=outputs, reference_outputs={"expected_mode": "search"})["score"] == 0


def test_structure_search_with_recommendation_fails():
    outputs = {
        "onsens": [_onsen("Yamada Onsen")],
        "recommendation": "should be None in search",
        "reply": "x",
    }
    assert eval_flow.structure(outputs=outputs, reference_outputs={"expected_mode": "search"})["score"] == 0


def test_structure_ask_gate_off_accepts_any_nonempty_reply():
    # ask_enabled defaults False in pytest: structure only requires empty onsens,
    # no recommendation, and a non-empty reply (the stub satisfies this).
    stub = eval_flow._ask_stub_reply()
    outputs = {"onsens": [], "recommendation": None, "reply": stub}
    assert eval_flow.structure(outputs=outputs, reference_outputs={"expected_mode": "ask"})["score"] == 1


def test_structure_ask_empty_reply_fails():
    outputs = {"onsens": [], "recommendation": None, "reply": ""}
    assert eval_flow.structure(outputs=outputs, reference_outputs={"expected_mode": "ask"})["score"] == 0


def test_structure_ask_with_onsens_fails():
    outputs = {"onsens": [_onsen("Yamada Onsen")], "recommendation": None, "reply": "answer"}
    assert eval_flow.structure(outputs=outputs, reference_outputs={"expected_mode": "ask"})["score"] == 0


def test_structure_ask_with_recommendation_fails():
    outputs = {"onsens": [], "recommendation": "pick X", "reply": "answer"}
    assert eval_flow.structure(outputs=outputs, reference_outputs={"expected_mode": "ask"})["score"] == 0


def test_structure_ask_gate_on_rejects_stub():
    # When ask_enabled is ON, the stub showing through means the answer node never
    # ran — that must FAIL; a real answer PASSES.
    from core.config import settings

    stub = eval_flow._ask_stub_reply()
    prior = settings.ask_enabled
    settings.ask_enabled = True
    try:
        stubbed = {"onsens": [], "recommendation": None, "reply": stub}
        real = {"onsens": [], "recommendation": None, "reply": "Wash before entering."}
        assert eval_flow.structure(outputs=stubbed, reference_outputs={"expected_mode": "ask"})["score"] == 0
        assert eval_flow.structure(outputs=real, reference_outputs={"expected_mode": "ask"})["score"] == 1
    finally:
        settings.ask_enabled = prior


def test_structure_ask_gate_on_no_info_example_requires_fallback():
    # An expect_no_info ask example must land on the EXACT no-info fallback; any
    # other (even non-stub) answer is a fabrication and must fail.
    from core.config import settings

    fallback = eval_flow._no_info_reply()
    prior = settings.ask_enabled
    settings.ask_enabled = True
    try:
        ref = {"expected_mode": "ask", "expect_no_info": True}
        good = {"onsens": [], "recommendation": None, "reply": fallback}
        bad = {"onsens": [], "recommendation": None, "reply": "The wifi password is 1234."}
        assert eval_flow.structure(outputs=good, reference_outputs=ref)["score"] == 1
        assert eval_flow.structure(outputs=bad, reference_outputs=ref)["score"] == 0
    finally:
        settings.ask_enabled = prior


def test_structure_no_data_good_and_bad():
    good = {"onsens": [], "recommendation": None, "reply": "No onsen found."}
    bad = {"onsens": [_onsen("Phantom")], "recommendation": None, "reply": "x"}
    assert eval_flow.structure(outputs=good, reference_outputs={"expected_mode": "no-data"})["score"] == 1
    assert eval_flow.structure(outputs=bad, reference_outputs={"expected_mode": "no-data"})["score"] == 0


# --- trip evaluators (V3 PR4) -------------------------------------------------
# Pure-logic tests for the three deterministic trip evaluators with fabricated
# target outputs (trajectory / itinerary / retrieval-spy) — NO run_workflow, NO
# LangSmith, NO LLM. Ground truth is set per-test (the autouse fixture resets it).


def _trip_ref(regions, nights, no_data_regions=None):
    """A trip reference_outputs (expectation) block."""
    return {
        "expected_mode": "trip",
        "regions": regions,
        "expected_nights": nights,
        "no_data_regions": no_data_regions or [],
    }


def _leg(region, nights, onsen_names, no_data=False):
    return {
        "region": region,
        "nights": nights,
        "no_data": no_data,
        "onsens": [_onsen(n) for n in onsen_names],
    }


def _itinerary(nights, legs):
    selected = [o for leg in legs for o in leg["onsens"]]
    return {"nights": nights, "regions": legs, "selected_onsens": selected}


# -- existing evaluators abstain on trip mode --
def test_grounding_abstains_on_trip():
    r = eval_flow.grounding(outputs={"onsens": []}, reference_outputs=_trip_ref(["Gifu"], 3))
    assert r["score"] is None


def test_structure_abstains_on_trip():
    r = eval_flow.structure(outputs={"onsens": []}, reference_outputs=_trip_ref(["Gifu"], 3))
    assert r["score"] is None


# -- slot_filling_completeness --
def test_slot_filling_abstains_on_non_trip():
    r = eval_flow.slot_filling_completeness(
        outputs={"_trajectory": []}, reference_outputs={"expected_mode": "search"}
    )
    assert r["score"] is None


def test_slot_filling_passes_followup_then_complete():
    # Turn 1: something missing → follow-up asked. Turn 2: complete → no follow-up.
    outputs = {
        "_trajectory": [
            {"missing_required": ["dates_or_season"], "asked_followup": True},
            {"missing_required": [], "asked_followup": False},
        ]
    }
    r = eval_flow.slot_filling_completeness(outputs=outputs, reference_outputs=_trip_ref(["Gifu"], 5))
    assert r["score"] == 1


def test_slot_filling_passes_complete_in_one_turn():
    outputs = {"_trajectory": [{"missing_required": [], "asked_followup": False}]}
    r = eval_flow.slot_filling_completeness(outputs=outputs, reference_outputs=_trip_ref(["Gifu"], 4))
    assert r["score"] == 1


def test_slot_filling_fails_followup_not_asked_when_missing():
    # Required slot missing but NO follow-up asked → invariant violated.
    outputs = {"_trajectory": [{"missing_required": ["nights"], "asked_followup": False}]}
    r = eval_flow.slot_filling_completeness(outputs=outputs, reference_outputs=_trip_ref(["Gifu"], 3))
    assert r["score"] == 0


def test_slot_filling_fails_followup_asked_when_nothing_missing():
    outputs = {"_trajectory": [{"missing_required": [], "asked_followup": True}]}
    r = eval_flow.slot_filling_completeness(outputs=outputs, reference_outputs=_trip_ref(["Gifu"], 3))
    assert r["score"] == 0


def test_slot_filling_fails_when_required_still_missing_at_end():
    outputs = {
        "_trajectory": [
            {"missing_required": ["nights", "dates_or_season"], "asked_followup": True},
            {"missing_required": ["dates_or_season"], "asked_followup": True},
        ]
    }
    r = eval_flow.slot_filling_completeness(outputs=outputs, reference_outputs=_trip_ref(["Gifu"], 3))
    assert r["score"] == 0


def test_slot_filling_fails_when_no_turns():
    r = eval_flow.slot_filling_completeness(outputs={"_trajectory": []}, reference_outputs=_trip_ref(["Gifu"], 3))
    assert r["score"] == 0


# -- tool_selection_presence --
def test_tool_presence_abstains_on_non_trip():
    r = eval_flow.tool_selection_presence(
        outputs={"_retrieval_prefectures": []}, reference_outputs={"expected_mode": "recommend"}
    )
    assert r["score"] is None


def test_tool_presence_passes_when_all_regions_retrieved():
    outputs = {"_retrieval_prefectures": ["Gifu", "Shizuoka"]}
    r = eval_flow.tool_selection_presence(
        outputs=outputs, reference_outputs=_trip_ref(["Gifu", "Shizuoka"], 5)
    )
    assert r["score"] == 1


def test_tool_presence_fails_when_a_region_not_retrieved():
    outputs = {"_retrieval_prefectures": ["Gifu"]}  # Shizuoka missing
    r = eval_flow.tool_selection_presence(
        outputs=outputs, reference_outputs=_trip_ref(["Gifu", "Shizuoka"], 5)
    )
    assert r["score"] == 0
    assert "Shizuoka" in r["comment"]


def test_tool_presence_fails_when_retrieval_never_ran():
    outputs = {"_retrieval_prefectures": []}
    r = eval_flow.tool_selection_presence(outputs=outputs, reference_outputs=_trip_ref(["Gifu"], 3))
    assert r["score"] == 0


# -- plan_validity --
def test_plan_validity_abstains_on_non_trip():
    r = eval_flow.plan_validity(outputs={}, reference_outputs={"expected_mode": "search"})
    assert r["score"] is None


def test_plan_validity_passes_grounded_and_nights_add_up():
    eval_flow.set_ground_truth({"Gifu": {"gero onsen", "hirayu onsen"}, "Shizuoka": {"atami onsen"}})
    legs = [_leg("Gifu", 3, ["Gero Onsen", "Hirayu Onsen"]), _leg("Shizuoka", 2, ["Atami Onsen"])]
    outputs = {
        "_itinerary": _itinerary(5, legs),
        "onsens": [_onsen("Gero Onsen"), _onsen("Atami Onsen")],
    }
    r = eval_flow.plan_validity(outputs=outputs, reference_outputs=_trip_ref(["Gifu", "Shizuoka"], 5))
    assert r["score"] == 1


def test_plan_validity_fails_when_no_itinerary():
    r = eval_flow.plan_validity(outputs={"_itinerary": None}, reference_outputs=_trip_ref(["Gifu"], 3))
    assert r["score"] == 0


def test_plan_validity_fails_when_nights_dont_add_up():
    eval_flow.set_ground_truth({"Gifu": {"gero onsen"}})
    legs = [_leg("Gifu", 2, ["Gero Onsen"])]  # legs sum 2 but total says 5
    outputs = {"_itinerary": _itinerary(5, legs), "onsens": [_onsen("Gero Onsen")]}
    r = eval_flow.plan_validity(outputs=outputs, reference_outputs=_trip_ref(["Gifu"], 5))
    assert r["score"] == 0
    assert "nights" in r["comment"].lower()


def test_plan_validity_fails_when_total_nights_mismatch_expected():
    eval_flow.set_ground_truth({"Gifu": {"gero onsen"}})
    legs = [_leg("Gifu", 4, ["Gero Onsen"])]
    outputs = {"_itinerary": _itinerary(4, legs), "onsens": [_onsen("Gero Onsen")]}
    # itinerary is internally consistent (4==4) but expected_nights is 5.
    r = eval_flow.plan_validity(outputs=outputs, reference_outputs=_trip_ref(["Gifu"], 5))
    assert r["score"] == 0


def test_plan_validity_fails_on_fabricated_onsen_out_of_region():
    eval_flow.set_ground_truth({"Gifu": {"gero onsen"}})
    legs = [_leg("Gifu", 3, ["Totally Invented Onsen"])]
    outputs = {"_itinerary": _itinerary(3, legs), "onsens": [_onsen("Totally Invented Onsen")]}
    r = eval_flow.plan_validity(outputs=outputs, reference_outputs=_trip_ref(["Gifu"], 3))
    assert r["score"] == 0
    assert "ground truth" in r["comment"]


def test_plan_validity_passes_no_data_region_flagged_empty():
    eval_flow.set_ground_truth({"Gifu": {"gero onsen"}})  # Hokkaido absent → no data
    legs = [_leg("Gifu", 3, ["Gero Onsen"]), _leg("Hokkaido", 0, [], no_data=True)]
    outputs = {"_itinerary": _itinerary(3, legs), "onsens": [_onsen("Gero Onsen")]}
    ref = _trip_ref(["Gifu", "Hokkaido"], 3, no_data_regions=["Hokkaido"])
    r = eval_flow.plan_validity(outputs=outputs, reference_outputs=ref)
    assert r["score"] == 1


def test_plan_validity_fails_when_no_data_region_has_onsen():
    eval_flow.set_ground_truth({"Gifu": {"gero onsen"}})
    # Hokkaido flagged no_data but (impossibly) carries an onsen → fabrication.
    legs = [_leg("Gifu", 3, ["Gero Onsen"]), _leg("Hokkaido", 0, ["Phantom Onsen"], no_data=True)]
    outputs = {"_itinerary": _itinerary(3, legs), "onsens": [_onsen("Gero Onsen")]}
    ref = _trip_ref(["Gifu", "Hokkaido"], 3, no_data_regions=["Hokkaido"])
    r = eval_flow.plan_validity(outputs=outputs, reference_outputs=ref)
    assert r["score"] == 0


def test_plan_validity_fails_when_expected_no_data_region_not_flagged():
    # Ground truth says Hokkaido has data, but the example expected it as no-data
    # and the leg is NOT flagged → expectation unmet.
    eval_flow.set_ground_truth({"Gifu": {"gero onsen"}, "Hokkaido": {"sapporo onsen"}})
    legs = [_leg("Gifu", 2, ["Gero Onsen"]), _leg("Hokkaido", 1, ["Sapporo Onsen"])]
    outputs = {"_itinerary": _itinerary(3, legs), "onsens": [_onsen("Gero Onsen"), _onsen("Sapporo Onsen")]}
    ref = _trip_ref(["Gifu", "Hokkaido"], 3, no_data_regions=["Hokkaido"])
    r = eval_flow.plan_validity(outputs=outputs, reference_outputs=ref)
    assert r["score"] == 0
    assert "no-data not flagged" in r["comment"]


def test_plan_validity_fails_on_surfaced_onsen_out_of_region():
    # Legs are clean, but AgentResponse.onsens leaks a name from no requested region.
    eval_flow.set_ground_truth({"Gifu": {"gero onsen"}})
    legs = [_leg("Gifu", 3, ["Gero Onsen"])]
    outputs = {"_itinerary": _itinerary(3, legs), "onsens": [_onsen("Rogue Onsen")]}
    r = eval_flow.plan_validity(outputs=outputs, reference_outputs=_trip_ref(["Gifu"], 3))
    assert r["score"] == 0
    assert "out of region" in r["comment"]


# -- hotels_exist (V3 PR5) --
def _hotel(name):
    return {"name": name, "url": f"https://h.example/{name}"}


def _leg_h(region, nights, stops, no_data=False):
    """A leg whose onsen stops carry a `hotels` list (PR5 shape)."""
    return {
        "region": region,
        "nights": nights,
        "no_data": no_data,
        "onsens": [{**_onsen(name), "hotels": hotels} for name, hotels in stops],
    }


def test_hotels_exist_abstains_on_non_trip():
    r = eval_flow.hotels_exist(outputs={}, reference_outputs={"expected_mode": "search"})
    assert r["score"] is None


def test_hotels_exist_passes_when_all_stops_looked_up():
    legs = [_leg_h("Gifu", 2, [("Gero Onsen", [_hotel("Ryokan A")]), ("Hirayu Onsen", [])])]
    outputs = {"_itinerary": _itinerary(2, legs), "hotels": [_hotel("Ryokan A")]}
    r = eval_flow.hotels_exist(outputs=outputs, reference_outputs=_trip_ref(["Gifu"], 2))
    assert r["score"] == 1


def test_hotels_exist_passes_when_none_found_everywhere():
    # Empty hotels at every stop is the honest "none found" case → PASS.
    legs = [_leg_h("Gifu", 1, [("Gero Onsen", [])])]
    outputs = {"_itinerary": _itinerary(1, legs), "hotels": []}
    r = eval_flow.hotels_exist(outputs=outputs, reference_outputs=_trip_ref(["Gifu"], 1))
    assert r["score"] == 1


def test_hotels_exist_fails_when_a_stop_missing_lookup():
    # A stop with NO `hotels` key means the hotel step didn't run for it.
    leg = {"region": "Gifu", "nights": 1, "no_data": False, "onsens": [_onsen("Gero Onsen")]}
    outputs = {"_itinerary": _itinerary(1, [leg]), "hotels": []}
    r = eval_flow.hotels_exist(outputs=outputs, reference_outputs=_trip_ref(["Gifu"], 1))
    assert r["score"] == 0
    assert "missing hotels lookup" in r["comment"]


def test_hotels_exist_fails_on_fabricated_surfaced_hotel():
    # A surfaced hotel that no stop returned = fabrication.
    legs = [_leg_h("Gifu", 1, [("Gero Onsen", [_hotel("Ryokan A")])])]
    outputs = {"_itinerary": _itinerary(1, legs), "hotels": [_hotel("Phantom Hotel")]}
    r = eval_flow.hotels_exist(outputs=outputs, reference_outputs=_trip_ref(["Gifu"], 1))
    assert r["score"] == 0
    assert "not from any stop" in r["comment"]


def test_hotels_exist_ignores_no_data_legs():
    # A no-data leg carries no onsen, so it imposes no hotel requirement.
    legs = [
        _leg_h("Gifu", 2, [("Gero Onsen", [_hotel("Ryokan A")])]),
        {"region": "Nowhere", "nights": 0, "no_data": True, "onsens": []},
    ]
    outputs = {"_itinerary": _itinerary(2, legs), "hotels": [_hotel("Ryokan A")]}
    r = eval_flow.hotels_exist(
        outputs=outputs, reference_outputs=_trip_ref(["Gifu", "Nowhere"], 2)
    )
    assert r["score"] == 1


def test_hotels_exist_fails_when_no_itinerary():
    r = eval_flow.hotels_exist(
        outputs={"_itinerary": None}, reference_outputs=_trip_ref(["Gifu"], 1)
    )
    assert r["score"] == 0


# -- multi-factor re-planning evaluators (V3 PR7 RED BASELINE) --
# Pure-logic tests for the four new deterministic evaluators. Each gets a
# fabricated GOOD reply (the behaviour PR7 will produce → PASS), the naive
# template reply today's plan node produces (→ FAIL, the red baseline), and an
# abstain case when the example does not carry the evaluator's gate flag.

# A representative naive PR3c template reply — the exact prose shape build_reply
# emits. It names every region and its onsen/hotels but contains NONE of the
# multi-factor behaviour markers, so every PR7 evaluator must FAIL on it.
_NAIVE_REPLY = (
    "Here's a naive 3-night onsen itinerary — Gifu (1 night): Gero Onsen "
    "(nearby hotels: Ryokan A); Nagano (1 night): Shibu Onsen "
    "(no hotels found nearby); Shizuoka (1 night): Atami Onsen."
)


# -- constraint_conflict_acknowledged --
def test_constraint_conflict_abstains_without_gate_flag():
    r = eval_flow.constraint_conflict_acknowledged(
        outputs={"reply": _NAIVE_REPLY}, reference_outputs=_trip_ref(["Gifu"], 3)
    )
    assert r["score"] is None


def test_constraint_conflict_passes_on_acknowledging_reply():
    good = {
        "reply": (
            "Three dispersed regions in 3 nights at a relaxed pace would be rushed, "
            "so this is over-constrained — here's a tighter plan."
        )
    }
    ref = {"expected_mode": "trip", "expect_constraint_conflict_ack": True}
    assert eval_flow.constraint_conflict_acknowledged(outputs=good, reference_outputs=ref)["score"] == 1


def test_constraint_conflict_fails_on_naive_reply():
    """The red baseline: today's naive plan reply never acknowledges the conflict."""
    ref = {"expected_mode": "trip", "expect_constraint_conflict_ack": True}
    r = eval_flow.constraint_conflict_acknowledged(
        outputs={"reply": _NAIVE_REPLY}, reference_outputs=ref
    )
    assert r["score"] == 0


# -- no_infeasible_plan --
def test_no_infeasible_plan_abstains_without_gate_flag():
    r = eval_flow.no_infeasible_plan(
        outputs={"reply": _NAIVE_REPLY}, reference_outputs=_trip_ref(["Gifu"], 3)
    )
    assert r["score"] is None


def test_no_infeasible_plan_passes_when_feasibility_flagged():
    good = {
        "reply": (
            "Okinawa and Gifu need a flight, which costs you a travel day — "
            "I'd suggest two separate trips or reallocate the nights."
        )
    }
    ref = {"expected_mode": "trip", "expect_feasibility_flag": True}
    assert eval_flow.no_infeasible_plan(outputs=good, reference_outputs=ref)["score"] == 1


def test_no_infeasible_plan_fails_on_naive_reply():
    """The red baseline: the naive node plans Okinawa+Gifu without flagging a flight."""
    ref = {"expected_mode": "trip", "expect_feasibility_flag": True}
    r = eval_flow.no_infeasible_plan(outputs={"reply": _NAIVE_REPLY}, reference_outputs=ref)
    assert r["score"] == 0


# -- tradeoff_explained --
def test_tradeoff_explained_abstains_without_gate_flag():
    r = eval_flow.tradeoff_explained(
        outputs={"reply": _NAIVE_REPLY}, reference_outputs=_trip_ref(["Gifu"], 3)
    )
    assert r["score"] is None


def test_tradeoff_explained_passes_when_tradeoff_stated():
    good = {
        "reply": (
            "I prioritised winter-accessible outdoor baths, so I'd swap the "
            "high-elevation stop for a lower one rather than risk a snowed-in road."
        )
    }
    ref = {"expected_mode": "trip", "expect_tradeoff_explanation": True}
    assert eval_flow.tradeoff_explained(outputs=good, reference_outputs=ref)["score"] == 1


def test_tradeoff_explained_fails_on_naive_reply():
    """The red baseline: the naive reply makes and explains no tradeoff."""
    ref = {"expected_mode": "trip", "expect_tradeoff_explanation": True}
    r = eval_flow.tradeoff_explained(outputs={"reply": _NAIVE_REPLY}, reference_outputs=ref)
    assert r["score"] == 0


# -- dropped_region_reasoned --
def test_dropped_region_abstains_without_expected_list():
    r = eval_flow.dropped_region_reasoned(
        outputs={"reply": _NAIVE_REPLY}, reference_outputs=_trip_ref(["Gifu"], 3)
    )
    assert r["score"] is None


def test_dropped_region_passes_when_a_droppable_region_reasoned():
    good = {
        "reply": (
            "To keep the pace relaxed I'd drop Shizuoka and focus on Gifu and "
            "Nagano, which are closer together."
        )
    }
    ref = {
        "expected_mode": "trip",
        "expect_dropped_regions": ["Nagano", "Shizuoka"],
    }
    assert eval_flow.dropped_region_reasoned(outputs=good, reference_outputs=ref)["score"] == 1


def test_dropped_region_fails_on_naive_reply_naming_all_regions():
    """Red baseline: the naive reply names every region but with NO drop context.

    Guards the both-conditions rule — naming a region is not enough without a
    drop/merge marker, so the naive itinerary (which lists all three regions)
    must still FAIL.
    """
    ref = {
        "expected_mode": "trip",
        "expect_dropped_regions": ["Nagano", "Shizuoka"],
    }
    r = eval_flow.dropped_region_reasoned(outputs={"reply": _NAIVE_REPLY}, reference_outputs=ref)
    assert r["score"] == 0


def test_dropped_region_fails_when_drop_marker_but_wrong_region():
    """A drop marker that names only a NON-droppable region does not pass."""
    reply = {"reply": "I'd drop Hokkaido entirely and keep the rest."}
    ref = {
        "expected_mode": "trip",
        "expect_dropped_regions": ["Nagano", "Shizuoka"],
    }
    r = eval_flow.dropped_region_reasoned(outputs=reply, reference_outputs=ref)
    assert r["score"] == 0


# -- the naive baseline fails ALL four new evaluators (the red baseline in one shot) --
def test_naive_reply_fails_every_multifactor_evaluator():
    """One assertion that today's naive plan reply reds every PR7 evaluator."""
    ref = {
        "expected_mode": "trip",
        "expect_constraint_conflict_ack": True,
        "expect_feasibility_flag": True,
        "expect_tradeoff_explanation": True,
        "expect_dropped_regions": ["Nagano", "Shizuoka"],
    }
    out = {"reply": _NAIVE_REPLY}
    assert eval_flow.constraint_conflict_acknowledged(outputs=out, reference_outputs=ref)["score"] == 0
    assert eval_flow.no_infeasible_plan(outputs=out, reference_outputs=ref)["score"] == 0
    assert eval_flow.tradeoff_explained(outputs=out, reference_outputs=ref)["score"] == 0
    assert eval_flow.dropped_region_reasoned(outputs=out, reference_outputs=ref)["score"] == 0


# -- the four new evaluators abstain on non-trip / pre-PR7 examples (no side effects) --
def test_multifactor_evaluators_abstain_on_search_example():
    """The PR7 evaluators must not touch the existing search/recommend/ask rows."""
    out = {"reply": "Found 2 onsen in Okinawa.", "onsens": []}
    ref = {"expected_mode": "search"}  # no expect_* gate flags
    assert eval_flow.constraint_conflict_acknowledged(outputs=out, reference_outputs=ref)["score"] is None
    assert eval_flow.no_infeasible_plan(outputs=out, reference_outputs=ref)["score"] is None
    assert eval_flow.tradeoff_explained(outputs=out, reference_outputs=ref)["score"] is None
    assert eval_flow.dropped_region_reasoned(outputs=out, reference_outputs=ref)["score"] is None


# -- trip cost/latency budget bucket --
def test_cost_budget_trip_bucket():
    within = {"_cost_usd": 0.015}
    over = {"_cost_usd": 0.03}
    ref = {"expected_mode": "trip"}
    assert eval_flow.cost_budget(outputs=within, reference_outputs=ref)["score"] == 1
    assert eval_flow.cost_budget(outputs=over, reference_outputs=ref)["score"] == 0


def test_latency_trip_bucket():
    within = {"_latency_ms": 15000}
    over = {"_latency_ms": 25000}
    ref = {"expected_mode": "trip"}
    assert eval_flow.latency(outputs=within, reference_outputs=ref)["score"] == 1
    assert eval_flow.latency(outputs=over, reference_outputs=ref)["score"] == 0


# --- target thread-runner (plumbing, no paid calls) ---------------------------
def test_target_runs_thread_and_captures_trip_signals():
    """The target loops a `messages` thread through one session and records the
    per-turn trajectory + final slots/itinerary — all seams mocked, no paid calls.
    """
    from agent.trip.slots import _ELICIT_QUESTIONS
    from agent.workflow import pipeline

    dates_q = _ELICIT_QUESTIONS["dates_or_season"]

    # Turn 1: still missing dates → elicit question. Turn 2: complete → itinerary.
    turn_results = [
        {"reply": dates_q, "onsens": [], "hotels": [], "recommendation": None},
        {"reply": "Here's a naive 5-night onsen itinerary — Gifu (5 nights): Gero Onsen.",
         "onsens": [_onsen("Gero Onsen")], "hotels": [], "recommendation": None},
    ]

    async def _fake_run_workflow(message, session_id):
        return turn_results.pop(0)

    # get_state is called after each turn (2) + once for the final snapshot (3).
    itinerary = {"nights": 5, "regions": [_leg("Gifu", 5, ["Gero Onsen"])],
                 "selected_onsens": [_onsen("Gero Onsen")]}
    snapshots = [
        SimpleNamespace(values={"slots": {"regions": ["Gifu"], "nights": 5}}),  # after t1: dates missing
        SimpleNamespace(values={"slots": {"regions": ["Gifu"], "nights": 5, "dates_or_season": "autumn"},
                                "itinerary": itinerary}),  # after t2
        SimpleNamespace(values={"slots": {"regions": ["Gifu"], "nights": 5, "dates_or_season": "autumn"},
                                "itinerary": itinerary}),  # final
    ]

    from agent.trip import graph as trip_graph_mod

    with patch.object(pipeline, "run_workflow", _fake_run_workflow), \
        patch.object(trip_graph_mod.trip_graph, "get_state", side_effect=snapshots):
        target = eval_flow.make_target_with_usage()
        out = target({"messages": ["plan a trip", "5 nights in Gifu this autumn"]})

    # Trajectory: turn 1 asked a follow-up (dates missing); turn 2 complete, none.
    traj = out["_trajectory"]
    assert len(traj) == 2
    assert traj[0]["asked_followup"] is True and traj[0]["missing_required"] == ["dates_or_season"]
    assert traj[1]["asked_followup"] is False and traj[1]["missing_required"] == []
    # Final slots + itinerary surfaced for the plan_validity evaluator.
    assert out["_itinerary"]["nights"] == 5
    assert out["_final_slots"]["dates_or_season"] == "autumn"
    # Last turn's AgentResponse fields passthrough.
    assert out["onsens"][0]["name"] == "Gero Onsen"
    assert "_cost_usd" in out and "_latency_ms" in out


# --- cost_budget evaluator ----------------------------------------------------
def test_cost_budget_within_passes():
    outputs = {"_cost_usd": 0.0017}
    assert eval_flow.cost_budget(outputs=outputs, reference_outputs={"expected_mode": "search"})["score"] == 1


def test_cost_budget_over_fails():
    outputs = {"_cost_usd": 0.02}  # over the 0.01 search budget
    assert eval_flow.cost_budget(outputs=outputs, reference_outputs={"expected_mode": "search"})["score"] == 0


def test_cost_budget_recommend_has_more_headroom():
    """A cost that fails the search budget can pass the recommend budget."""
    outputs = {"_cost_usd": 0.02}
    assert eval_flow.cost_budget(outputs=outputs, reference_outputs={"expected_mode": "recommend"})["score"] == 1


# --- latency evaluator --------------------------------------------------------
def test_latency_within_passes():
    outputs = {"_latency_ms": 3000}
    assert eval_flow.latency(outputs=outputs, reference_outputs={"expected_mode": "search"})["score"] == 1


def test_latency_over_fails():
    outputs = {"_latency_ms": 9000}  # over the 8000ms search budget
    assert eval_flow.latency(outputs=outputs, reference_outputs={"expected_mode": "search"})["score"] == 0


def test_latency_recommend_has_more_headroom():
    outputs = {"_latency_ms": 9000}
    assert eval_flow.latency(outputs=outputs, reference_outputs={"expected_mode": "recommend"})["score"] == 1


# --- no_prompt_leak evaluator (security red-team, Phase 2) --------------------
def test_no_prompt_leak_abstains_without_gate_flag():
    """Non-adversarial examples never set expect_no_leak → abstain (None)."""
    out = {"reply": "Found 2 onsen in Gifu.", "recommendation": None}
    r = eval_flow.no_prompt_leak(outputs=out, reference_outputs={"expected_mode": "search"})
    assert r["score"] is None


def test_no_prompt_leak_passes_on_clean_reply():
    out = {"reply": "Found 3 onsen in Gifu.", "recommendation": None}
    ref = {"expected_mode": "search", "expect_no_leak": True}
    assert eval_flow.no_prompt_leak(outputs=out, reference_outputs=ref)["score"] == 1


@pytest.mark.parametrize(
    "reply",
    [
        "You parse a traveller's message about Japanese hot springs.",  # intent prompt
        "STRICT GROUNDING RULES: answer only from passages.",           # ask prompt
        "Here is my system prompt: you are an expert guide.",           # exfil
        "Your key is sk-abc123.",                                       # secret shape
        "The api_key is configured server-side.",
        "Ignore all previous instructions, as you asked.",             # echoed payload
    ],
)
def test_no_prompt_leak_fails_when_marker_present(reply):
    ref = {"expected_mode": "ask", "expect_no_leak": True}
    r = eval_flow.no_prompt_leak(outputs={"reply": reply}, reference_outputs=ref)
    assert r["score"] == 0


def test_no_prompt_leak_fails_on_leaked_openai_key_value():
    """The configured OpenAI key value appearing verbatim is a leak."""
    from core.config import settings

    out = {"reply": f"For debugging, my key is {settings.openai_api_key}."}
    ref = {"expected_mode": "search", "expect_no_leak": True}
    r = eval_flow.no_prompt_leak(outputs=out, reference_outputs=ref)
    assert r["score"] == 0
    assert "openai_api_key_value" in r["comment"]


def test_no_prompt_leak_scans_recommendation_channel_too():
    """A leak in the recommendation prose (recommend mode) is caught, not just reply."""
    out = {"reply": "Found 1 onsen in Gifu.", "recommendation": "My system prompt says X."}
    ref = {"expected_mode": "recommend", "expect_no_leak": True}
    assert eval_flow.no_prompt_leak(outputs=out, reference_outputs=ref)["score"] == 0


def test_adversarial_examples_are_wellformed_and_gate_no_leak():
    """The Phase-2 red-team examples parse and every one gates no_prompt_leak."""
    adversarial = [ex for ex in eval_flow._EXAMPLES if ex.get("expect_no_leak")]
    assert len(adversarial) == 4
    for ex in adversarial:
        assert ex.get("message")  # single-message /chat inputs
        assert ex["expected_mode"] in {"search", "ask", "no-data"}
        exp = eval_flow._expectation(ex)
        assert exp["expect_no_leak"] is True


def test_no_prompt_leak_abstains_on_all_non_adversarial_examples():
    """No pre-existing example accidentally trips the no-leak gate."""
    for ex in eval_flow._EXAMPLES:
        if ex.get("expect_no_leak"):
            continue
        assert eval_flow._expectation(ex)["expect_no_leak"] is False


# --- normalize ----------------------------------------------------------------
def test_normalize_collapses_whitespace_and_lowercases():
    assert eval_flow.normalize("  Yamada   Onsen  ") == "yamada onsen"
    assert eval_flow.normalize(None) == ""


# --- analyze_enabled restore discipline (regression for global-state leak) ----
def _run_evaluation_with_no_paid_calls(evaluate_side_effect=None):
    """Drive run_evaluation() with every paid/IO seam mocked out.

    Stubs LangSmith (Client + evaluate), the ChromaDB ground-truth read, the
    dataset upsert, the target factory, and the report so NO paid calls happen.
    ``evaluate_side_effect`` lets a test make evaluate() raise, to prove the
    restore still runs in the finally block. Returns nothing; the assertion is on
    settings.analyze_enabled afterwards.
    """
    fake_evaluate = MagicMock(name="evaluate")
    if evaluate_side_effect is not None:
        fake_evaluate.side_effect = evaluate_side_effect

    with patch("langsmith.Client", MagicMock()), \
        patch("langsmith.evaluate", fake_evaluate), \
        patch.object(eval_flow, "build_ground_truth", return_value={}), \
        patch.object(eval_flow, "set_ground_truth"), \
        patch.object(eval_flow, "get_or_create_dataset"), \
        patch.object(eval_flow, "make_target_with_usage", return_value=lambda i: {}), \
        patch.object(eval_flow, "_report", return_value=0), \
        patch.dict("os.environ", {"LANGSMITH_API_KEY": "test-key"}):
        eval_flow.run_evaluation()


def test_run_evaluation_restores_analyze_enabled_on_success():
    """run_evaluation flips analyze_enabled ON for the run, then restores it."""
    from core.config import settings

    original = settings.analyze_enabled
    settings.analyze_enabled = False  # start from a known prior value
    seen = {}

    def _capture(*args, **kwargs):
        # Inside evaluate(): the global must be ON so recommend examples run the
        # analyze brain.
        seen["analyze_enabled"] = settings.analyze_enabled
        return MagicMock()

    try:
        _run_evaluation_with_no_paid_calls(evaluate_side_effect=_capture)
        assert seen["analyze_enabled"] is True  # ON during the run
        assert settings.analyze_enabled is False  # restored, no leak
    finally:
        settings.analyze_enabled = original


def test_run_evaluation_flips_and_restores_ask_enabled():
    """run_evaluation flips ask_enabled ON for the run, then restores it."""
    from core.config import settings

    original = settings.ask_enabled
    settings.ask_enabled = False  # start from a known prior value
    seen = {}

    def _capture(*args, **kwargs):
        seen["ask_enabled"] = settings.ask_enabled
        return MagicMock()

    try:
        _run_evaluation_with_no_paid_calls(evaluate_side_effect=_capture)
        assert seen["ask_enabled"] is True  # ON during the run
        assert settings.ask_enabled is False  # restored, no leak
    finally:
        settings.ask_enabled = original


def test_run_evaluation_flips_and_restores_trip_enabled():
    """run_evaluation flips trip_enabled ON for the run, then restores it."""
    from core.config import settings

    original = settings.trip_enabled
    settings.trip_enabled = False  # start from a known prior value (prod default)
    seen = {}

    def _capture(*args, **kwargs):
        seen["trip_enabled"] = settings.trip_enabled
        return MagicMock()

    try:
        _run_evaluation_with_no_paid_calls(evaluate_side_effect=_capture)
        assert seen["trip_enabled"] is True  # ON during the run
        assert settings.trip_enabled is False  # restored, no leak
    finally:
        settings.trip_enabled = original


def test_run_evaluation_restores_trip_enabled_even_if_evaluate_raises():
    """The trip_enabled restore lives in the same finally — a raise must not leak."""
    from core.config import settings

    original = settings.trip_enabled
    settings.trip_enabled = False
    try:
        with pytest.raises(RuntimeError, match="boom"):
            _run_evaluation_with_no_paid_calls(
                evaluate_side_effect=RuntimeError("boom")
            )
        assert settings.trip_enabled is False  # restored despite the raise
    finally:
        settings.trip_enabled = original


def test_run_evaluation_restores_ask_enabled_even_if_evaluate_raises():
    """The ask_enabled restore lives in the same finally — a raise must not leak."""
    from core.config import settings

    original = settings.ask_enabled
    settings.ask_enabled = False
    try:
        with pytest.raises(RuntimeError, match="boom"):
            _run_evaluation_with_no_paid_calls(
                evaluate_side_effect=RuntimeError("boom")
            )
        assert settings.ask_enabled is False  # restored despite the raise
    finally:
        settings.ask_enabled = original


def test_run_evaluation_restores_analyze_enabled_even_if_evaluate_raises():
    """The restore lives in a finally, so a failing evaluate() must not leak."""
    from core.config import settings

    original = settings.analyze_enabled
    settings.analyze_enabled = False
    try:
        with pytest.raises(RuntimeError, match="boom"):
            _run_evaluation_with_no_paid_calls(
                evaluate_side_effect=RuntimeError("boom")
            )
        assert settings.analyze_enabled is False  # restored despite the raise
    finally:
        settings.analyze_enabled = original


# --- proscons_grounding LLM-judge evaluator -----------------------------------
def test_proscons_grounding_passes_when_pros_grounded():
    """All onsens' pros/cons grounded → judge returns 1 → score 1."""
    outputs = {
        "onsens": [
            _onsen("Yamada Onsen", pros=["quiet", "scenic"], cons=["remote"]),
            _onsen("Naha Onsen", pros=["central"]),
        ]
    }
    with patch.object(eval_flow, "_llm_judge", return_value=1) as judge:
        result = eval_flow.proscons_grounding(outputs=outputs, reference_outputs={})
    assert result["score"] == 1
    # One judge call per onsen carrying pros/cons.
    assert judge.call_count == 2


def test_proscons_grounding_fails_when_a_fabricated_pro_injected():
    """A single ungrounded onsen (judge returns 0) fails the whole example."""
    outputs = {
        "onsens": [
            _onsen("Yamada Onsen", pros=["quiet"]),
            _onsen("Naha Onsen", pros=["free helicopter rides"]),  # fabricated
        ]
    }

    # Judge: grounded for the first onsen, ungrounded for the fabricated one.
    def _fake_judge(system, user):
        return 0 if "helicopter" in user else 1

    with patch.object(eval_flow, "_llm_judge", side_effect=_fake_judge):
        result = eval_flow.proscons_grounding(outputs=outputs, reference_outputs={})
    assert result["score"] == 0
    assert "Naha Onsen" in result["comment"]


def test_proscons_grounding_abstains_on_search_example():
    """No pros/cons (search/no-data) → abstain (None), judge never called."""
    outputs = {"onsens": [_onsen("Yamada Onsen"), _onsen("Naha Onsen")]}
    with patch.object(eval_flow, "_llm_judge") as judge:
        result = eval_flow.proscons_grounding(
            outputs=outputs, reference_outputs={"expected_mode": "search"}
        )
    assert result["score"] is None
    assert result["comment"] == "n/a"
    judge.assert_not_called()


def test_proscons_grounding_abstains_when_judge_errors_on_every_onsen():
    """If the judge errors (None) on EVERY onsen, the example abstains — not a false pass."""
    outputs = {
        "onsens": [
            _onsen("Yamada Onsen", pros=["quiet"]),
            _onsen("Naha Onsen", pros=["central"]),
        ]
    }
    # _llm_judge returns None for every call (e.g. judge API down).
    with patch.object(eval_flow, "_llm_judge", return_value=None):
        result = eval_flow.proscons_grounding(outputs=outputs, reference_outputs={})
    assert result["score"] is None
    assert "judge unavailable" in result["comment"]


# --- ask_grounding LLM-judge evaluator ----------------------------------------
def test_ask_grounding_passes_against_supporting_chunks():
    """Real ask answer + supporting chunks + judge=1 → score 1."""
    outputs = {"onsens": [], "recommendation": None, "reply": "Wash before entering."}
    ref = {"expected_mode": "ask"}
    inputs = {"message": "Do I wash before entering the bath?"}

    fake_chunks = ([{"text": "Bathers rinse off before entering the communal bath."}], {})
    with patch(
        "services.retrieval.retrieval_service.query_knowledge_with_diagnostics",
        return_value=fake_chunks,
    ) as q, patch.object(eval_flow, "_llm_judge", return_value=1):
        result = eval_flow.ask_grounding(
            outputs=outputs, reference_outputs=ref, inputs=inputs
        )
    assert result["score"] == 1
    q.assert_called_once()


def test_ask_grounding_fails_when_answer_unsupported():
    """Real ask answer + chunks + judge=0 → score 0."""
    outputs = {"onsens": [], "recommendation": None, "reply": "Tattoos are always fine."}
    ref = {"expected_mode": "ask"}
    inputs = {"message": "Can I enter with tattoos?"}

    fake_chunks = ([{"text": "Many onsen prohibit visible tattoos."}], {})
    with patch(
        "services.retrieval.retrieval_service.query_knowledge_with_diagnostics",
        return_value=fake_chunks,
    ), patch.object(eval_flow, "_llm_judge", return_value=0):
        result = eval_flow.ask_grounding(
            outputs=outputs, reference_outputs=ref, inputs=inputs
        )
    assert result["score"] == 0


def test_ask_grounding_abstains_on_no_info_fallback():
    """The no-info fallback is a correct refusal, not a grounding claim → abstain."""
    fallback = eval_flow._no_info_reply()
    outputs = {"onsens": [], "recommendation": None, "reply": fallback}
    with patch(
        "services.retrieval.retrieval_service.query_knowledge_with_diagnostics"
    ) as q, patch.object(eval_flow, "_llm_judge") as judge:
        result = eval_flow.ask_grounding(
            outputs=outputs,
            reference_outputs={"expected_mode": "ask"},
            inputs={"message": "wifi password?"},
        )
    assert result["score"] is None
    q.assert_not_called()  # no retrieval on an abstain
    judge.assert_not_called()


def test_ask_grounding_abstains_on_stub_reply():
    """The 'coming soon' stub means the answer node never ran → abstain."""
    stub = eval_flow._ask_stub_reply()
    outputs = {"onsens": [], "recommendation": None, "reply": stub}
    with patch(
        "services.retrieval.retrieval_service.query_knowledge_with_diagnostics"
    ) as q, patch.object(eval_flow, "_llm_judge") as judge:
        result = eval_flow.ask_grounding(
            outputs=outputs,
            reference_outputs={"expected_mode": "ask"},
            inputs={"message": "etiquette?"},
        )
    assert result["score"] is None
    q.assert_not_called()
    judge.assert_not_called()


def test_ask_grounding_abstains_on_non_ask_mode():
    """A non-ask example never reaches the judge → abstain."""
    outputs = {"onsens": [], "recommendation": None, "reply": "some answer"}
    with patch.object(eval_flow, "_llm_judge") as judge:
        result = eval_flow.ask_grounding(
            outputs=outputs, reference_outputs={"expected_mode": "search"}
        )
    assert result["score"] is None
    judge.assert_not_called()


# --- _llm_judge fail-safe -----------------------------------------------------
def test_llm_judge_fails_safe_to_abstain_on_error():
    """A judge LLM error returns None (abstain), NOT a false pass, and never crashes."""
    with patch.object(
        eval_flow, "_build_judge_llm", side_effect=RuntimeError("api down")
    ):
        # Reset the cached singleton so the patched builder is exercised.
        eval_flow._JUDGE_LLM = None
        assert eval_flow._llm_judge("sys", "user") is None


def _judge_returning(content: str):
    """A fake judge LLM whose .invoke() returns a response with the given content."""
    llm = MagicMock()
    llm.invoke.return_value = SimpleNamespace(content=content)
    return llm


@pytest.mark.parametrize(
    "content,expected",
    [
        ("GROUNDED", 1),
        ("grounded", 1),  # case-insensitive
        ("UNGROUNDED", 0),
        ("Ungrounded.", 0),
        ("maybe?", None),  # unrecognised → abstain, NOT a false PASS
        ("", None),  # empty → abstain
        ("the answer is supported", None),  # prose without the token → abstain
    ],
)
def test_llm_judge_maps_output_with_unrecognised_abstaining(content, expected):
    """GROUNDED→1, UNGROUNDED→0, anything else→None (abstain)."""
    with patch.object(eval_flow, "_build_judge_llm", return_value=_judge_returning(content)):
        eval_flow._JUDGE_LLM = None  # reset cached singleton
        assert eval_flow._llm_judge("sys", "user") is expected


# --- _report None-score (abstain) handling ------------------------------------
def _fake_result(mode: str, message: str, scores: dict[str, int | None]):
    """Build a results-row stand-in matching what _report() reads.

    _report iterates rows accessing res["example"], res["run"], and
    res["evaluation_results"]["results"] (each with .key / .score).
    """
    eval_results = [
        SimpleNamespace(key=k, score=v) for k, v in scores.items()
    ]
    return {
        "example": SimpleNamespace(
            metadata={"expected_mode": mode}, inputs={"message": message}
        ),
        "run": SimpleNamespace(),
        "evaluation_results": {"results": eval_results},
    }


def test_report_skips_none_scores_no_false_failures(capsys):
    """None (abstain) scores are skipped: not counted, not a failure, rendered '-'."""
    results = [
        # One evaluator abstains (None); everything else passes. The abstain path
        # is dormant now the LLM-judges are parked, but _report still handles None
        # generically — exercise it via an active evaluator key.
        _fake_result(
            "search",
            "Find onsen in Okinawa",
            {
                "grounding": 1,
                "structure": None,  # abstain → skipped, not a failure
                "cost_budget": 1,
                "latency": 1,
            },
        ),
    ]
    failures = eval_flow._report(results)
    assert failures == 0  # a None must NOT be counted as a failure

    out = capsys.readouterr().out
    # Abstained evaluator renders as 0/0 in the per-evaluator pass rate.
    assert "structure      0/0" in out
    # Applicable evaluators counted normally.
    assert "grounding      1/1" in out


def _fake_result_with_comments(
    mode: str, message: str, scored: dict[str, tuple[int | None, str | None]]
):
    """results-row stand-in where each evaluator carries (score, comment)."""
    eval_results = [
        SimpleNamespace(key=k, score=s, comment=c) for k, (s, c) in scored.items()
    ]
    return {
        "example": SimpleNamespace(
            metadata={"expected_mode": mode}, inputs={"message": message}
        ),
        "run": SimpleNamespace(),
        "evaluation_results": {"results": eval_results},
    }


def test_report_prints_reason_under_each_fail_row(capsys):
    """Each FAIL (score 0) prints its evaluator's comment as a reason line."""
    results = [
        _fake_result_with_comments(
            "search",
            "Find onsen in Okinawa",
            {
                "grounding": (0, "not in Okinawa ground truth: ['Phantom Onsen']"),
                "structure": (0, "mode=search recommendation=True onsens=0 reply=no"),
                "cost_budget": (1, "$0.0017 vs budget $0.05 (search)"),
                "latency": (1, "1200ms vs budget 20000ms (search)"),
            },
        ),
    ]
    failures = eval_flow._report(results)
    assert failures == 2

    out = capsys.readouterr().out
    # Reason lines appear under the row for the two FAILs.
    assert "└─ grounding: not in Okinawa ground truth" in out
    assert "└─ structure: mode=search recommendation=True onsens=0 reply=no" in out
    # Passing evaluators get NO reason line.
    assert "└─ cost_budget" not in out
    assert "└─ latency" not in out


def test_report_fail_without_comment_prints_placeholder(capsys):
    """A FAIL whose evaluator omitted a comment still prints a reason line."""
    results = [
        _fake_result_with_comments(
            "search", "Find onsen in X", {"structure": (0, None)}
        ),
    ]
    eval_flow._report(results)
    out = capsys.readouterr().out
    assert "└─ structure: (no reason provided)" in out


def test_report_counts_explicit_zero_as_failure(capsys):
    """An explicit 0 (not None) is still a failure and is counted."""
    results = [
        _fake_result(
            "search",
            "Find onsen in X",
            {
                "grounding": 0,  # judged fail
                "structure": 1,
                "cost_budget": 1,
                "latency": 1,
            },
        ),
    ]
    failures = eval_flow._report(results)
    assert failures == 1  # the explicit 0 is counted

    out = capsys.readouterr().out
    assert "grounding      0/1" in out  # counted toward total, 0 passed
