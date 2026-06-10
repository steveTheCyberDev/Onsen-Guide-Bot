"""Tests for the V2.5 ADDITIVE schema fields.

The recommend feature adds fields that must NOT regress existing outputs:
  - OnsenResult.pros / OnsenResult.cons default to empty lists.
  - AgentResponse.recommendation defaults to None.
  - ChatResponse (api.routes.chat) carries a recommendation field.

These are pure constructor/serialization checks — no mocking.
"""

from agent.agent import AgentResponse, OnsenResult


def _onsen(**overrides):
    base = dict(name="Beppu Onsen", spring_type="Sulfur", spa_quality="Sulfur spring")
    base.update(overrides)
    return OnsenResult(**base)


# --- OnsenResult.pros / cons -----------------------------------------------


def test_onsen_pros_cons_default_empty():
    # Arrange / Act
    onsen = _onsen()
    # Assert — additive fields default to empty lists (search/ReAct unaffected).
    assert onsen.pros == []
    assert onsen.cons == []


def test_onsen_pros_cons_accept_values():
    # Arrange / Act
    onsen = _onsen(pros=["scenic"], cons=["crowded"])
    # Assert
    assert onsen.pros == ["scenic"]
    assert onsen.cons == ["crowded"]


def test_onsen_pros_cons_serialize():
    # Arrange / Act
    dumped = _onsen(pros=["a"], cons=["b"]).model_dump()
    # Assert
    assert dumped["pros"] == ["a"] and dumped["cons"] == ["b"]


# --- AgentResponse.recommendation ------------------------------------------


def test_agent_response_recommendation_defaults_none():
    # Arrange / Act
    resp = AgentResponse(reply="ok")
    # Assert
    assert resp.recommendation is None


def test_agent_response_recommendation_roundtrips():
    # Arrange / Act
    resp = AgentResponse(reply="ok", recommendation="Pick Beppu.")
    # Assert
    assert resp.recommendation == "Pick Beppu."
    assert resp.model_dump()["recommendation"] == "Pick Beppu."


# --- ChatResponse carries recommendation -----------------------------------


def test_chat_response_has_recommendation_field():
    # Arrange — import here so app construction stays out of module import time.
    from api.routes.chat import ChatResponse

    # Act
    resp = ChatResponse(reply="ok", recommendation="Pick Beppu.")
    # Assert
    assert "recommendation" in resp.model_dump()
    assert resp.recommendation == "Pick Beppu."


def test_chat_response_recommendation_defaults_none():
    # Arrange
    from api.routes.chat import ChatResponse

    # Act
    resp = ChatResponse(reply="ok")
    # Assert — additive: omitting it must not break existing callers.
    assert resp.recommendation is None
