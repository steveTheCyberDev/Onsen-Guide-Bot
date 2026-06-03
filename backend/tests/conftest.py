"""Shared pytest fixtures for the Onsen Guide Bot backend test suite.

pytest is configured (see pytest.ini) to run from the `backend/` directory, so
all imports are relative to that root, e.g. `from services.chat...`.
"""

import os

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


@pytest.fixture
def client():
    """FastAPI TestClient that authenticates by default.

    Importing the app pulls in `agent.agent`, which constructs a ChatOpenAI
    client and a LangGraph react agent at import time. No network call is made
    on import, so this is safe; tests that hit `/chat` mock `run_agent`.

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
