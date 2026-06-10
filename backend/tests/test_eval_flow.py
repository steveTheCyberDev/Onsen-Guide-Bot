"""Unit tests for the eval_flow harness — pure functions only, NO paid calls.

These exercise the evaluator functions and the ground-truth helper with
synthetic ``AgentResponse``-shaped dicts and a mocked ChromaDB collection. They
do NOT import/run ``run_workflow`` and do NOT touch LangSmith, so the suite stays
free + fast. The live experiment (paid) lives in ``scripts/eval_flow.py``.
"""

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


def test_structure_ask_good():
    stub = eval_flow._ask_stub_reply()
    outputs = {"onsens": [], "recommendation": None, "reply": stub}
    assert eval_flow.structure(outputs=outputs, reference_outputs={"expected_mode": "ask"})["score"] == 1


def test_structure_ask_with_onsens_fails():
    stub = eval_flow._ask_stub_reply()
    outputs = {"onsens": [_onsen("Yamada Onsen")], "recommendation": None, "reply": stub}
    assert eval_flow.structure(outputs=outputs, reference_outputs={"expected_mode": "ask"})["score"] == 0


def test_structure_no_data_good_and_bad():
    good = {"onsens": [], "recommendation": None, "reply": "No onsen found."}
    bad = {"onsens": [_onsen("Phantom")], "recommendation": None, "reply": "x"}
    assert eval_flow.structure(outputs=good, reference_outputs={"expected_mode": "no-data"})["score"] == 1
    assert eval_flow.structure(outputs=bad, reference_outputs={"expected_mode": "no-data"})["score"] == 0


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
