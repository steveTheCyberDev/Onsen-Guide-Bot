"""Shared pytest fixtures + helpers for the Onsen Guide Bot backend test suite.

pytest is configured (see pytest.ini) to run from the `backend/` directory, so
all imports are relative to that root, e.g. `from services.chat...`.

Besides the autouse fixtures, this module exports the shared TRIP-PLANNER test
helpers (`trip_cfg`, `trip_onsen`, `trip_slots_llm`, `trip_retrieval`,
`trip_retrieval_by_region`). They are plain functions, not fixtures, because
several trip tests need them at module import time; import them explicitly:

    from tests.conftest import trip_cfg, trip_onsen, trip_slots_llm
"""

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure required settings exist before any module that builds `Settings()` or a
# `ChatOpenAI` client is imported. These are dummy values used only for import;
# every test that exercises external I/O mocks the network layer.
os.environ.setdefault("OPENAI_API_KEY", "test-openai-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("GOOGLE_MAPS_API_KEY", "test-google-key")
os.environ.setdefault("RAKUTEN_APP_ID", "test-rakuten-app-id")
os.environ.setdefault("RAKUTEN_ACCESS_KEY", "test-rakuten-access-key")
os.environ.setdefault("RAKUTEN_HOTEL_URL", "https://example.com/rakuten")
os.environ.setdefault("API_KEY", "test-api-key")

# The valid key for the test suite, kept in sync with the API_KEY env above.
TEST_API_KEY = "test-api-key"

# A realistic set of ingested Japanese prefectures for tests. The trip-planner
# graph validates regions against services.retrieval.known_prefectures (the
# ingested set) — see the region-validation feature (2026-07-12 "reject early").
# We never want the graph to hit real Chroma for that in the suite, so the autouse
# fixture below patches the name the graph imported to this fixed set. It INCLUDES
# every prefecture the existing trip tests use (so their routing is unchanged) and
# EXCLUDES non-Japan places (so region-validation tests can assert rejection).
TEST_KNOWN_PREFECTURES = frozenset(
    {"Gifu", "Nagano", "Shizuoka", "Oita", "Kyoto", "Hokkaido", "Okinawa"}
)


@pytest.fixture(autouse=True)
def _patch_known_prefectures():
    """Patch the known-prefecture lookup to a fixed set (no Chroma).

    Autouse so every test that validates a region against the ingested set uses
    ``TEST_KNOWN_PREFECTURES`` instead of the live collection. Two consumers resolve
    the name at call time and are patched here:
      - the trip graph (``agent.trip.graph``) — region validation before the plan
        node (both the singleton graph and fresh ``build_trip_graph`` instances);
      - the workflow pipeline (``agent.workflow.pipeline``) — the reflected-echo
        guard in ``_build_reply`` only echoes a prefecture that is in this set.
    Region-validation tests override the graph binding with their own ``patch`` when
    they need a specific valid/invalid split. The real service function — and its own
    unit test — stay untouched.
    """
    from agent.trip import graph as trip_graph_module
    from agent.workflow import pipeline as workflow_pipeline

    with patch.object(
        trip_graph_module,
        "known_prefectures",
        return_value=TEST_KNOWN_PREFECTURES,
    ), patch.object(
        workflow_pipeline,
        "known_prefectures",
        return_value=TEST_KNOWN_PREFECTURES,
    ):
        yield


@pytest.fixture(autouse=True)
def _patch_trip_search_hotels():
    """Stub the trip plan node's hotel lookup so the suite never hits Rakuten.

    PR5's ``plan`` node calls ``search_hotels`` (Rakuten) for each selected onsen
    stop. Autouse-patch the name bound INSIDE ``agent.trip.itinerary`` to return no
    hotels by default, so existing trip tests stay deterministic + free (no network)
    and behave as "no hotels found nearby". PR5 hotel tests override this with their
    own ``patch`` to return hotels or raise (fail-soft). Leaves the real Rakuten
    service — and its own tests — untouched.
    """
    from agent.trip import itinerary as trip_itinerary

    with patch.object(trip_itinerary, "search_hotels", return_value=[]):
        yield


@pytest.fixture
def client():
    """FastAPI TestClient that authenticates by default.

    Importing the app pulls in the workflow engine (`agent/workflow/pipeline.py`),
    which constructs ChatOpenAI clients at import time. No network call is made on
    import, so this is safe; tests that hit `/chat` mock `run_workflow`.

    The valid X-API-Key header is attached to every request so existing route
    tests don't each have to set it; auth-specific behaviour is covered by the
    `unauth_client` fixture and test_auth.py.
    """
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app, headers={"X-API-Key": TEST_API_KEY}) as test_client:
        yield test_client


@pytest.fixture
def unauth_client():
    """FastAPI TestClient with NO default API key header — for auth tests."""
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# Shared trip-planner test helpers
#
# Previously hand-rolled in each of the nine tests/test_trip_*.py files, with the
# `_onsen` signature drifting four different ways. Consolidated here so a change
# to the record shape is one edit, not nine.
# ---------------------------------------------------------------------------


def trip_cfg(session_id: str) -> dict:
    """LangGraph config addressing one checkpointed conversation thread."""
    return {"configurable": {"thread_id": session_id}}


def trip_onsen(
    name: str,
    prefecture: str = "Gifu",
    lat: float = 36.0,
    lng: float = 137.0,
) -> dict:
    """A ``query_onsen_structured``-shaped record, as the plan node consumes it.

    `lat`/`lng` default to a generic in-Japan point; tests whose assertions depend
    on real geometry (the haversine spread / infeasibility rules) MUST pass the
    region's actual centroid explicitly rather than relying on the default.

    Note `location` is carried through the pipeline as an opaque string — nothing
    parses it — so its exact format is not load-bearing.
    """
    return {
        "name": name,
        "location": f"City, {prefecture}",
        "spring_type": "Sulfur Spring",
        "spa_quality": f"{name} is a relaxing sulfur spring.",
        "detail_url": f"https://example.com/{name.replace(' ', '-')}",
        "lat": lat,
        "lng": lng,
    }


def trip_slots_llm(*updates) -> MagicMock:
    """Stand-in for the module-level ``agent.trip.slots._llm`` extraction LLM.

    ``extract_slots`` awaits ``_llm.ainvoke`` exactly once per turn. Pass one
    ``SlotUpdate`` per turn to script a multi-turn conversation; the LAST update
    repeats if the graph runs more turns than were scripted, which is what
    single-update callers ("this same delta every turn") rely on. With no
    arguments every turn yields an empty delta — for tests that only care about
    turn_count accumulation, not slot content.
    """
    from agent.trip.slots import SlotUpdate

    script = list(updates) or [SlotUpdate()]

    def _next(*_args, **_kwargs):
        return script.pop(0) if len(script) > 1 else script[0]

    llm = MagicMock()
    llm.ainvoke = AsyncMock(side_effect=_next)
    return llm


def trip_retrieval(*records: dict) -> MagicMock:
    """Mock ``query_onsen_structured`` returning the SAME records for every region."""
    return MagicMock(return_value=list(records))


def trip_retrieval_by_region(mapping: dict[str, list[dict]]) -> MagicMock:
    """Mock ``query_onsen_structured`` keyed by the ``prefecture`` kwarg.

    The plan node calls the service once per region with ``prefecture=region``;
    this returns that region's records, or ``[]`` for a region absent from
    ``mapping`` (the no-data case). Records are copied per call so a test that
    mutates a returned record cannot leak into a later call.
    """

    def _fn(query, prefecture=None, n_results=20):
        return [dict(r) for r in mapping.get(prefecture, [])]

    return MagicMock(side_effect=_fn)
