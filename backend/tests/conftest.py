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


@pytest.fixture
def client():
    """FastAPI TestClient bound to the real app.

    Importing the app pulls in `agent.agent`, which constructs a ChatOpenAI
    client and a LangGraph react agent at import time. No network call is made
    on import, so this is safe; tests that hit `/chat` mock `run_agent`.
    """
    from fastapi.testclient import TestClient

    from api.main import app

    with TestClient(app) as test_client:
        yield test_client
