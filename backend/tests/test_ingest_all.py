"""Tests for the combined deploy ingest entrypoint (scripts/ingest_all.py)."""

from unittest.mock import patch

from scripts import ingest_all


def test_runs_both_ingests_in_order():
    with patch.object(ingest_all.subprocess, "run") as run:
        ingest_all.main()

    assert run.call_count == 2
    # Each call is subprocess.run([sys.executable, "-m", <module>], check=True);
    # the module name is the last element of the argv list.
    modules = [call.args[0][-1] for call in run.call_args_list]
    assert modules == ["scripts.ingest_regions", "scripts.ingest_knowledge"]
    # KB ingest must run after the onsen ingest.
    assert modules.index("scripts.ingest_knowledge") > modules.index(
        "scripts.ingest_regions"
    )


def test_each_ingest_invoked_with_check_true():
    # check=True so a failing sub-ingest aborts the job loudly (non-zero exit)
    # rather than silently leaving a half-populated DB.
    with patch.object(ingest_all.subprocess, "run") as run:
        ingest_all.main()

    assert all(call.kwargs.get("check") is True for call in run.call_args_list)


def test_aborts_if_first_ingest_fails():
    # If the onsen ingest fails, the KB ingest must NOT run (check=True raises).
    with patch.object(
        ingest_all.subprocess, "run", side_effect=RuntimeError("boom")
    ) as run:
        try:
            ingest_all.main()
        except RuntimeError:
            pass
    assert run.call_count == 1
