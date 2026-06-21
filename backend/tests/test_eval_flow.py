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
