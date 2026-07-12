"""Shared pytest fixtures for the Onsen Guide Bot backend test suite.

pytest is configured (see pytest.ini) to run from the `backend/` directory, so
all imports are relative to that root, e.g. `from services.chat...`.
"""

import os
from unittest.mock import patch

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
    """Patch the trip graph's known-prefecture lookup to a fixed set (no Chroma).

    Autouse so every test that drives the trip graph (directly or via plan_trip)
    validates regions against ``TEST_KNOWN_PREFECTURES`` instead of the live
    collection. Region-validation tests override this with their own ``patch`` when
    they need a specific valid/invalid split. Patches the name bound INSIDE
    ``agent.trip.graph`` (both the singleton graph and fresh ``build_trip_graph``
    instances resolve it at call time), leaving the real service function — and its
    own unit test — untouched.
    """
    from agent.trip import graph as trip_graph_module

    with patch.object(
        trip_graph_module,
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
