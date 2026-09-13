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


# -- multi-round conversation-state evaluators (M2) ----------------------------
# Pure-logic tests for the five deterministic multi-round evaluators, built on
# hand-made trajectories — NO run_workflow, NO LangSmith, NO LLM. Two trajectories
# recur: _FIXED_TRACE (post-M1 behaviour) and _BROKEN_TRACE (the exact pre-M1
# production failure from LangSmith thread 779ace6d — "Hokkaido only" only ever
# ADDED, and the reply repeated the previous turn verbatim). Every evaluator is
# asserted green on the first and red on the second, so these tests fail if M1 is
# ever regressed AND fail if an evaluator is rigged to always pass.

# The stable, non-region slots a narrowing turn must not touch.
_BASE_SLOTS = {
    "nights": 3,
    "dates_or_season": "autumn",
    "party": "couple",
    "budget": "mid",
    "pace": "relaxed",
    "spring_or_scenery_prefs": "",
    "must_haves": [],
    "mobility_transport": "mixed",
}


def _turn_state(
    regions,
    reply,
    *,
    message="",
    slots=None,
    dropped=None,
    infeasible=None,
    missing=None,
    asked=False,
):
    """One trajectory entry, in the exact shape make_target_with_usage() builds."""
    return {
        "missing_required": missing or [],
        "asked_followup": asked,
        "message": message,
        "slots": {**_BASE_SLOTS, **(slots or {}), "regions": list(regions)},
        "regions": list(regions),
        "reply": reply,
        "dropped_regions": list(dropped or []),
        "infeasible_regions": sorted(infeasible or []),
    }


_PLAIN_REPLY = "Here's a naive 3-night onsen itinerary — Nagano (2 nights): Nozawa Onsen; Gifu (1 night): Gero Onsen."
_CONFLICT_REPLY = (
    "Heads-up: combining Nagano with Hokkaido isn't feasible in one land trip, so "
    "I'd drop Hokkaido. Here's a naive 3-night onsen itinerary — Nagano (2 nights): "
    "Nozawa Onsen; Gifu (1 night): Gero Onsen."
)
_HOKKAIDO_REPLY = (
    "Here's a naive 3-night onsen itinerary — Hokkaido (3 nights): Noboribetsu Onsen."
)

# The post-M1 trace: settle Nagano+Gifu → ADD Hokkaido (conflict fires, outlier
# dropped) → REPLACE with "Hokkaido only" (narrows, fresh plan, scratch state reset).
_FIXED_TRACE = [
    _turn_state(["Nagano", "Gifu"], _PLAIN_REPLY, message="3 nights in Nagano and Gifu"),
    _turn_state(
        ["Nagano", "Gifu", "Hokkaido"],
        _CONFLICT_REPLY,
        message="What about Hokkaido?",
        dropped=["Hokkaido"],
        infeasible=["Nagano", "Hokkaido"],
    ),
    _turn_state(["Hokkaido"], _HOKKAIDO_REPLY, message="Actually, Hokkaido only"),
]

# The pre-M1 trace: turn 3's REPLACE only ADDED (regions unchanged) and the reply
# was the previous turn's stale conflict message, word for word.
_BROKEN_TRACE = [
    _FIXED_TRACE[0],
    _FIXED_TRACE[1],
    _turn_state(
        ["Nagano", "Gifu", "Hokkaido"],
        _CONFLICT_REPLY,
        message="Actually, Hokkaido only",
        dropped=["Hokkaido"],
        infeasible=["Nagano", "Hokkaido"],
    ),
]

# The reference block the M2 dataset example carries (mirrors _EXAMPLES entry ⑤).
_TRACE_REF = {
    "expected_mode": "trip",
    "expect_turn_transitions": [
        {
            "turn": 1,
            "op": "add",
            "expected_regions": ["Nagano", "Gifu", "Hokkaido"],
            "expect_slots_unchanged": True,
            "expect_fresh_reply": True,
        },
        {
            "turn": 2,
            "op": "replace",
            "expected_regions": ["Hokkaido"],
            "expect_regions_gone": ["Nagano", "Gifu"],
            "expect_slots_unchanged": True,
            "expect_fresh_reply": True,
        },
    ],
}


def _trace_outputs(trajectory, final_regions=None, itinerary_regions=None):
    """Target-shaped outputs around a trajectory (final slots + itinerary legs)."""
    last = trajectory[-1]["regions"]
    legs = [
        _leg(r, 3 // max(len(itinerary_regions or last), 1), [])
        for r in (itinerary_regions if itinerary_regions is not None else last)
    ]
    return {
        "reply": trajectory[-1]["reply"],
        "_trajectory": trajectory,
        "_final_slots": {**_BASE_SLOTS, "regions": list(final_regions or last)},
        "_itinerary": _itinerary(3, legs),
    }


# -- state_transition_correctness --
def test_state_transition_abstains_without_transition_expectations():
    r = eval_flow.state_transition_correctness(
        outputs=_trace_outputs(_FIXED_TRACE), reference_outputs=_trip_ref(["Gifu"], 3)
    )
    assert r["score"] is None


def test_state_transition_passes_on_the_fixed_trace():
    r = eval_flow.state_transition_correctness(
        outputs=_trace_outputs(_FIXED_TRACE), reference_outputs=_TRACE_REF
    )
    assert r["score"] == 1


def test_state_transition_fails_when_replace_only_added():
    """The M1 defect: 'Hokkaido only' left Nagano+Gifu in the region set."""
    r = eval_flow.state_transition_correctness(
        outputs=_trace_outputs(_BROKEN_TRACE), reference_outputs=_TRACE_REF
    )
    assert r["score"] == 0
    assert "turn 2" in r["comment"] and "replace" in r["comment"]


def test_state_transition_is_order_and_case_insensitive():
    """Region ORDER is not a correctness property — only the SET is."""
    traj = list(_FIXED_TRACE)
    traj[1] = _turn_state(["hokkaido", "GIFU", "nagano"], _CONFLICT_REPLY)
    r = eval_flow.state_transition_correctness(
        outputs=_trace_outputs(traj), reference_outputs=_TRACE_REF
    )
    assert r["score"] == 1


def test_state_transition_fails_when_a_flagged_turn_never_ran():
    """A thread that ended early cannot silently pass a per-turn expectation."""
    r = eval_flow.state_transition_correctness(
        outputs=_trace_outputs(_FIXED_TRACE[:1]), reference_outputs=_TRACE_REF
    )
    assert r["score"] == 0
    assert "never ran" in r["comment"]


# -- state_preservation --
def test_state_preservation_abstains_without_gate_flag():
    ref = {"expected_mode": "trip", "expect_turn_transitions": [{"turn": 1, "op": "add"}]}
    r = eval_flow.state_preservation(
        outputs=_trace_outputs(_FIXED_TRACE), reference_outputs=ref
    )
    assert r["score"] is None


def test_state_preservation_passes_when_only_regions_changed():
    r = eval_flow.state_preservation(
        outputs=_trace_outputs(_FIXED_TRACE), reference_outputs=_TRACE_REF
    )
    assert r["score"] == 1


def test_state_preservation_fails_when_a_narrowing_turn_resets_another_slot():
    """Narrowing the regions must not quietly discard nights/dates already given."""
    traj = list(_FIXED_TRACE)
    traj[2] = _turn_state(
        ["Hokkaido"], _HOKKAIDO_REPLY, slots={"nights": None, "dates_or_season": None}
    )
    r = eval_flow.state_preservation(
        outputs=_trace_outputs(traj), reference_outputs=_TRACE_REF
    )
    assert r["score"] == 0
    assert "nights" in r["comment"] and "turn 2" in r["comment"]


def test_state_preservation_honours_the_expect_slots_changed_allowlist():
    """A turn that legitimately supplies another slot declares it and still passes."""
    traj = [
        _turn_state(["Gifu", "Nagano"], _PLAIN_REPLY),
        _turn_state(
            ["Gifu", "Nagano"], _PLAIN_REPLY, slots={"spring_or_scenery_prefs": "sulfur"}
        ),
    ]
    allowed = {
        "expected_mode": "trip",
        "expect_turn_transitions": [
            {
                "turn": 1,
                "op": "none",
                "expect_slots_unchanged": True,
                "expect_slots_changed": ["spring_or_scenery_prefs"],
            }
        ],
    }
    assert eval_flow.state_preservation(
        outputs=_trace_outputs(traj), reference_outputs=allowed
    )["score"] == 1
    # Without the allowlist the same turn is (correctly) a preservation failure.
    strict = {
        "expected_mode": "trip",
        "expect_turn_transitions": [
            {"turn": 1, "op": "none", "expect_slots_unchanged": True}
        ],
    }
    assert eval_flow.state_preservation(
        outputs=_trace_outputs(traj), reference_outputs=strict
    )["score"] == 0


def test_state_preservation_unions_the_allowlist_with_the_default():
    """`expect_slots_changed` ADDS to the default — it must not drop "regions".

    A turn that both narrows the regions AND states a preference is the natural
    authoring case, and the natural way to write it is to declare only the NEW
    slot. If the allowlist overrode the default instead of extending it, the
    (legitimate) region change would fail as an "unrelated slot changed".
    """
    traj = [
        _turn_state(["Gifu", "Nagano", "Hokkaido"], _PLAIN_REPLY),
        _turn_state(
            ["Gifu"], _PLAIN_REPLY, slots={"spring_or_scenery_prefs": "sulfur"}
        ),
    ]
    ref = {
        "expected_mode": "trip",
        "expect_turn_transitions": [
            {
                "turn": 1,
                "op": "replace",
                "expect_slots_unchanged": True,
                # Note: "regions" deliberately NOT re-declared here.
                "expect_slots_changed": ["spring_or_scenery_prefs"],
            }
        ],
    }
    r = eval_flow.state_preservation(outputs=_trace_outputs(traj), reference_outputs=ref)
    assert r["score"] == 1, r["comment"]
    # ...and the guard is still live: a slot outside the union still fails.
    traj[1]["slots"]["nights"] = None
    assert eval_flow.state_preservation(
        outputs=_trace_outputs(traj), reference_outputs=ref
    )["score"] == 0


def test_state_preservation_fails_on_turn_zero_with_no_predecessor():
    ref = {
        "expected_mode": "trip",
        "expect_turn_transitions": [
            {"turn": 0, "op": "replace", "expect_slots_unchanged": True}
        ],
    }
    r = eval_flow.state_preservation(
        outputs=_trace_outputs(_FIXED_TRACE), reference_outputs=ref
    )
    assert r["score"] == 0
    assert "no preceding turn" in r["comment"]


# -- correction_applied --
def test_correction_applied_abstains_without_a_gone_list():
    ref = {
        "expected_mode": "trip",
        "expect_turn_transitions": [
            {"turn": 2, "op": "replace", "expected_regions": ["Hokkaido"]}
        ],
    }
    r = eval_flow.correction_applied(
        outputs=_trace_outputs(_FIXED_TRACE), reference_outputs=ref
    )
    assert r["score"] is None


def test_correction_applied_passes_when_the_old_regions_are_gone():
    r = eval_flow.correction_applied(
        outputs=_trace_outputs(_FIXED_TRACE), reference_outputs=_TRACE_REF
    )
    assert r["score"] == 1


def test_correction_applied_fails_when_a_dropped_region_survives():
    """The most direct test of the M1 bug — REPLACE that only added."""
    r = eval_flow.correction_applied(
        outputs=_trace_outputs(_BROKEN_TRACE), reference_outputs=_TRACE_REF
    )
    assert r["score"] == 0
    assert "still in slots" in r["comment"]


def test_correction_applied_fails_when_the_itinerary_still_plans_a_dropped_region():
    """Slots narrowed but the plan didn't — the correction never reached the output."""
    outputs = _trace_outputs(_FIXED_TRACE, itinerary_regions=["Nagano", "Hokkaido"])
    r = eval_flow.correction_applied(outputs=outputs, reference_outputs=_TRACE_REF)
    assert r["score"] == 0
    assert "itinerary still plans" in r["comment"]


def test_correction_applied_fails_when_final_slots_still_carry_a_dropped_region():
    outputs = _trace_outputs(_FIXED_TRACE, final_regions=["Hokkaido", "Gifu"])
    r = eval_flow.correction_applied(outputs=outputs, reference_outputs=_TRACE_REF)
    assert r["score"] == 0
    assert "final slots" in r["comment"]


# -- latest_question_answered --
def test_latest_question_answered_abstains_without_gate_flag():
    ref = {
        "expected_mode": "trip",
        "expect_turn_transitions": [{"turn": 2, "op": "replace"}],
    }
    r = eval_flow.latest_question_answered(
        outputs=_trace_outputs(_FIXED_TRACE), reference_outputs=ref
    )
    assert r["score"] is None


def test_latest_question_answered_passes_on_a_fresh_reply():
    r = eval_flow.latest_question_answered(
        outputs=_trace_outputs(_FIXED_TRACE), reference_outputs=_TRACE_REF
    )
    assert r["score"] == 1


def test_latest_question_answered_fails_on_a_verbatim_repeat():
    """The literal trace symptom: the same conflict message returned twice."""
    r = eval_flow.latest_question_answered(
        outputs=_trace_outputs(_BROKEN_TRACE), reference_outputs=_TRACE_REF
    )
    assert r["score"] == 0
    assert "replayed turn 1's reply verbatim" in r["comment"]


def test_latest_question_answered_treats_whitespace_reflow_as_a_repeat():
    """Re-wrapping the same text is not answering the new question."""
    traj = list(_FIXED_TRACE)
    traj[2] = _turn_state(["Hokkaido"], "  " + _CONFLICT_REPLY.replace(" ", "  ") + "\n")
    r = eval_flow.latest_question_answered(
        outputs=_trace_outputs(traj), reference_outputs=_TRACE_REF
    )
    assert r["score"] == 0


def test_latest_question_answered_fails_on_an_empty_reply():
    traj = list(_FIXED_TRACE)
    traj[2] = _turn_state(["Hokkaido"], "   ")
    r = eval_flow.latest_question_answered(
        outputs=_trace_outputs(traj), reference_outputs=_TRACE_REF
    )
    assert r["score"] == 0
    assert "replied with nothing" in r["comment"]


# -- cross_turn_consistency --
# The flip side of latest_question_answered: M1 reset the per-turn re-plan scratch,
# and "reset" must mean RECOMPUTE, not FORGET.
_STABLE_REF = {
    "expected_mode": "trip",
    "expect_turn_transitions": [
        {
            "turn": 1,
            "op": "none",
            "expected_regions": ["Gifu", "Nagano", "Hokkaido"],
            "expect_same_verdict": True,
            "expect_slots_unchanged": True,
            "expect_slots_changed": ["spring_or_scenery_prefs"],
        }
    ],
}
_STABLE_TRACE = [
    _turn_state(
        ["Gifu", "Nagano", "Hokkaido"],
        _CONFLICT_REPLY,
        dropped=["Hokkaido"],
        infeasible=["Nagano", "Hokkaido"],
    ),
    _turn_state(
        ["Gifu", "Nagano", "Hokkaido"],
        _CONFLICT_REPLY,
        slots={"spring_or_scenery_prefs": "sulfur springs"},
        dropped=["Hokkaido"],
        infeasible=["Nagano", "Hokkaido"],
    ),
]


def test_cross_turn_consistency_abstains_without_gate_flag():
    r = eval_flow.cross_turn_consistency(
        outputs=_trace_outputs(_FIXED_TRACE), reference_outputs=_TRACE_REF
    )
    assert r["score"] is None


def test_cross_turn_consistency_passes_when_the_verdict_is_re_derived():
    r = eval_flow.cross_turn_consistency(
        outputs=_trace_outputs(_STABLE_TRACE), reference_outputs=_STABLE_REF
    )
    assert r["score"] == 1


def test_cross_turn_consistency_fails_when_an_unresolved_conflict_goes_quiet():
    """The over-correction guard: resetting the scratch state must not FORGET."""
    traj = list(_STABLE_TRACE)
    traj[1] = _turn_state(
        ["Gifu", "Nagano", "Hokkaido"],
        _PLAIN_REPLY,
        slots={"spring_or_scenery_prefs": "sulfur springs"},
    )
    r = eval_flow.cross_turn_consistency(
        outputs=_trace_outputs(traj), reference_outputs=_STABLE_REF
    )
    assert r["score"] == 0
    assert "DIFFERENT verdict" in r["comment"]


def test_cross_turn_consistency_fails_when_the_regions_actually_changed():
    """A mis-authored example (regions moved) is a FAIL, not a silent pass."""
    traj = list(_STABLE_TRACE)
    traj[1] = _turn_state(["Gifu"], _PLAIN_REPLY)
    r = eval_flow.cross_turn_consistency(
        outputs=_trace_outputs(traj), reference_outputs=_STABLE_REF
    )
    assert r["score"] == 0
    assert "premise does not hold" in r["comment"]


# -- the pre-M1 trace reds the whole multi-round block in one shot --
def test_pre_m1_trace_fails_every_multiround_evaluator():
    """One assertion that the real production failure is caught by the new gate."""
    out = _trace_outputs(_BROKEN_TRACE)
    assert eval_flow.state_transition_correctness(outputs=out, reference_outputs=_TRACE_REF)["score"] == 0
    assert eval_flow.correction_applied(outputs=out, reference_outputs=_TRACE_REF)["score"] == 0
    assert eval_flow.latest_question_answered(outputs=out, reference_outputs=_TRACE_REF)["score"] == 0
    # state_preservation still passes — the pre-M1 bug lost the CHANGE, not the
    # other slots. Asserted so the block documents what each evaluator does and
    # does NOT claim (no evaluator is a catch-all).
    assert eval_flow.state_preservation(outputs=out, reference_outputs=_TRACE_REF)["score"] == 1


def test_fixed_trace_greens_every_multiround_evaluator():
    """The mirror: post-M1 behaviour passes all four applicable evaluators."""
    out = _trace_outputs(_FIXED_TRACE)
    for evaluator in (
        eval_flow.state_transition_correctness,
        eval_flow.state_preservation,
        eval_flow.correction_applied,
        eval_flow.latest_question_answered,
    ):
        assert evaluator(outputs=out, reference_outputs=_TRACE_REF)["score"] == 1


# -- the five abstain everywhere they don't apply --
def test_multiround_evaluators_abstain_on_a_search_example():
    out = {"reply": "Found 2 onsen in Okinawa.", "onsens": []}
    ref = {"expected_mode": "search"}  # no expect_turn_transitions at all
    for evaluator in (
        eval_flow.state_transition_correctness,
        eval_flow.state_preservation,
        eval_flow.correction_applied,
        eval_flow.latest_question_answered,
        eval_flow.cross_turn_consistency,
    ):
        assert evaluator(outputs=out, reference_outputs=ref)["score"] is None


def test_pre_m2_examples_carry_no_turn_transitions():
    """Only the new M2 threads gate the multi-round evaluators; everything else abstains."""
    with_transitions = [
        ex for ex in eval_flow._EXAMPLES if ex.get("expect_turn_transitions")
    ]
    assert len(with_transitions) == 2
    for ex in eval_flow._EXAMPLES:
        exp = eval_flow._expectation(ex)
        assert "expect_turn_transitions" in exp  # key always present (default [])
        if ex not in with_transitions:
            assert exp["expect_turn_transitions"] == []
            out = {"reply": "x", "_trajectory": []}
            for evaluator in (
                eval_flow.state_transition_correctness,
                eval_flow.state_preservation,
                eval_flow.correction_applied,
                eval_flow.latest_question_answered,
                eval_flow.cross_turn_consistency,
            ):
                assert evaluator(outputs=out, reference_outputs=exp)["score"] is None


def test_m2_examples_are_wellformed_threads():
    """The two M2 dataset examples are complete trip threads with valid turn indices."""
    m2 = [ex for ex in eval_flow._EXAMPLES if ex.get("expect_turn_transitions")]
    assert len(m2) == 2
    valid_ops = {"replace", "add", "remove", "none"}
    gates = set()
    for ex in m2:
        assert ex["expected_mode"] == "trip"
        assert len(ex["messages"]) >= 2  # multi-ROUND by definition
        assert ex["expected_nights"] and ex["regions"]
        # No PR7 gate flags: those scan the FINAL reply, which for the narrowing
        # thread is a clean itinerary with no conflict prose (see the example note).
        assert not ex.get("conflict_factors")
        for entry in eval_flow._expectation(ex)["expect_turn_transitions"]:
            # A per-turn expectation must address a turn the thread actually has,
            # and can never target turn 0 (every check is relative to a predecessor
            # or to a change the opener cannot have made).
            assert 0 < entry["turn"] < len(ex["messages"])
            assert entry["op"] in valid_ops
            gates |= {k for k in entry if k.startswith("expect_")}
    # Between them the two examples gate all five multi-round evaluators.
    assert gates == {
        "expect_regions_gone",
        "expect_slots_unchanged",
        "expect_slots_changed",
        "expect_fresh_reply",
        "expect_same_verdict",
    }


def test_every_evaluator_has_a_report_column_label():
    """_report derives its table from EVALUATORS + _COLUMN_LABELS — keep them in sync."""
    for evaluator in eval_flow.EVALUATORS:
        assert evaluator.__name__ in eval_flow._COLUMN_LABELS


# -- trip cost/latency budget bucket --
# The trip constants are PER TURN and scaled by len(_trajectory): a trip example is
# a multi-turn thread and every settled turn pays for an analyze_model call, so a
# flat per-thread ceiling would silently tighten as an example grows turns.
def test_cost_budget_trip_bucket_is_per_turn():
    """Same $0.018 thread: over budget at 1 turn, within it at 3 turns."""
    ref = {"expected_mode": "trip"}
    three_turns = [{}, {}, {}]
    # ~3 settled turns at the measured ~$0.006 each.
    assert eval_flow.cost_budget(
        outputs={"_cost_usd": 0.018, "_trajectory": three_turns}, reference_outputs=ref
    )["score"] == 1
    # The same spend in a single turn is a real regression.
    assert eval_flow.cost_budget(
        outputs={"_cost_usd": 0.018, "_trajectory": [{}]}, reference_outputs=ref
    )["score"] == 0
    # Still catches a blow-out that scales past the per-turn ceiling.
    assert eval_flow.cost_budget(
        outputs={"_cost_usd": 0.05, "_trajectory": three_turns}, reference_outputs=ref
    )["score"] == 0


def test_latency_trip_bucket_is_per_turn():
    ref = {"expected_mode": "trip"}
    three_turns = [{}, {}, {}]
    assert eval_flow.latency(
        outputs={"_latency_ms": 25000, "_trajectory": three_turns}, reference_outputs=ref
    )["score"] == 1
    assert eval_flow.latency(
        outputs={"_latency_ms": 25000, "_trajectory": [{}]}, reference_outputs=ref
    )["score"] == 0
    assert eval_flow.latency(
        outputs={"_latency_ms": 45000, "_trajectory": three_turns}, reference_outputs=ref
    )["score"] == 0


def test_trip_budgets_fall_back_to_one_turn_without_a_trajectory():
    """max(1, ...) — a missing/empty trajectory must not collapse the budget to zero."""
    ref = {"expected_mode": "trip"}
    assert eval_flow.cost_budget(
        outputs={"_cost_usd": 0.006, "_trajectory": []}, reference_outputs=ref
    )["score"] == 1
    assert eval_flow.latency(outputs={"_latency_ms": 9000}, reference_outputs=ref)["score"] == 1


def test_non_trip_budgets_ignore_trajectory_length():
    """search/recommend/ask/no-data keep their flat per-run budgets, unscaled."""
    long_trace = [{}, {}, {}, {}, {}]
    for mode in ("search", "recommend", "ask", "no-data"):
        ref = {"expected_mode": mode}
        flat_cost = eval_flow.COST_BUDGET_USD[mode]
        flat_ms = eval_flow.LATENCY_BUDGET_MS[mode]
        assert eval_flow.cost_budget(
            outputs={"_cost_usd": flat_cost * 1.5, "_trajectory": long_trace},
            reference_outputs=ref,
        )["score"] == 0
        assert eval_flow.latency(
            outputs={"_latency_ms": int(flat_ms * 1.5), "_trajectory": long_trace},
            reference_outputs=ref,
        )["score"] == 0


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


def test_target_trajectory_captures_per_turn_conversation_state():
    """M2: each trajectory entry records the turn's regions, slots, reply + verdict.

    Replays the production narrowing trace through the target with every seam
    mocked (no paid calls) and asserts the captured trajectory is rich enough for
    the five multi-round evaluators to score it — then runs two of them on it
    end-to-end, which is what ties the capture change to the evaluators.
    """
    from agent.trip import graph as trip_graph_mod
    from agent.workflow import pipeline

    replies = [
        "Here's a naive 3-night onsen itinerary — Nagano (2 nights): Nozawa Onsen.",
        "Heads-up: combining Nagano with Hokkaido isn't feasible. Here's a naive "
        "3-night onsen itinerary — Nagano (2 nights): Nozawa Onsen.",
        "Here's a naive 3-night onsen itinerary — Hokkaido (3 nights): Noboribetsu Onsen.",
    ]

    async def _fake_run_workflow(message, session_id):
        return {"reply": replies.pop(0), "onsens": [], "hotels": [], "recommendation": None}

    def _values(regions, dropped=None, infeasible=None, itinerary=None):
        return SimpleNamespace(
            values={
                "slots": {"regions": regions, "nights": 3, "dates_or_season": "autumn"},
                "dropped_regions": [{"region": r, "reason": "far"} for r in (dropped or [])],
                "infeasible": infeasible,
                "itinerary": itinerary,
            }
        )

    final_itinerary = {
        "nights": 3,
        "regions": [_leg("Hokkaido", 3, ["Noboribetsu Onsen"])],
        "selected_onsens": [_onsen("Noboribetsu Onsen")],
    }
    # One get_state per turn (3) + one for the final snapshot.
    snapshots = [
        _values(["Nagano", "Gifu"]),
        _values(
            ["Nagano", "Gifu", "Hokkaido"],
            dropped=["Hokkaido"],
            infeasible={"regions": ["Nagano", "Hokkaido"], "leg_km": 812.0},
        ),
        _values(["Hokkaido"], itinerary=final_itinerary),
        _values(["Hokkaido"], itinerary=final_itinerary),
    ]

    with patch.object(pipeline, "run_workflow", _fake_run_workflow), patch.object(
        trip_graph_mod.trip_graph, "get_state", side_effect=snapshots
    ):
        target = eval_flow.make_target_with_usage()
        out = target(
            {
                "messages": [
                    "Plan a relaxed 3-night onsen trip across Nagano and Gifu this autumn.",
                    "What about adding Hokkaido to the trip?",
                    "Actually, make the trip Hokkaido only — drop Nagano and Gifu.",
                ]
            }
        )

    traj = out["_trajectory"]
    assert len(traj) == 3
    # Per-turn regions — the state_transition_correctness / correction_applied input.
    assert [t["regions"] for t in traj] == [
        ["Nagano", "Gifu"], ["Nagano", "Gifu", "Hokkaido"], ["Hokkaido"],
    ]
    # Full slot snapshot per turn — the state_preservation input.
    assert all(t["slots"]["nights"] == 3 for t in traj)
    assert traj[2]["slots"]["dates_or_season"] == "autumn"
    # Per-turn reply — the latest_question_answered input.
    assert traj[2]["reply"].startswith("Here's a naive") and traj[2]["reply"] != traj[1]["reply"]
    # The turn's message is recorded alongside the state it produced.
    assert traj[1]["message"] == "What about adding Hokkaido to the trip?"
    # Per-turn conflict verdict (names only) — the cross_turn_consistency input.
    assert traj[1]["dropped_regions"] == ["Hokkaido"]
    assert traj[1]["infeasible_regions"] == ["Hokkaido", "Nagano"]  # sorted
    assert traj[2]["dropped_regions"] == [] and traj[2]["infeasible_regions"] == []
    # The original slot-filling signals are unchanged (no regression for PR4).
    assert all(t["missing_required"] == [] and t["asked_followup"] is False for t in traj)

    # End-to-end: the captured trajectory scores green against the dataset example's
    # own reference block.
    ref = eval_flow._expectation(
        next(
            ex
            for ex in eval_flow._EXAMPLES
            if ex.get("expect_turn_transitions")
            and any(e.get("expect_regions_gone") for e in ex["expect_turn_transitions"])
        )
    )
    assert eval_flow.state_transition_correctness(outputs=out, reference_outputs=ref)["score"] == 1
    assert eval_flow.correction_applied(outputs=out, reference_outputs=ref)["score"] == 1
    assert eval_flow.latest_question_answered(outputs=out, reference_outputs=ref)["score"] == 1
    assert eval_flow.state_preservation(outputs=out, reference_outputs=ref)["score"] == 1


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
