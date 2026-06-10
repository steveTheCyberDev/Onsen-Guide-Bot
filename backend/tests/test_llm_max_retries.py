"""Wiring tests: ChatOpenAI instances use settings.llm_max_retries.

Both the ReAct llm (agent/agent.py) and the intent llm
(agent/workflow/intent.py) must construct their ChatOpenAI with
`max_retries == settings.llm_max_retries` so transient OpenAI errors are
retried a bounded number of times. These are light wiring checks — no real
network call. The already-constructed module-level instance is inspected for
agent.agent; for the structured-output-wrapped intent llm we reload the module
under a patched ChatOpenAI and assert the constructor kwargs.
"""

import importlib
from unittest.mock import MagicMock, patch

from core.config import settings


def test_agent_llm_uses_settings_max_retries():
    # Arrange / Act — the module-level instance is built at import time
    import agent.agent as agent_mod

    # Assert
    assert agent_mod.llm.max_retries == settings.llm_max_retries


def test_intent_llm_constructed_with_settings_max_retries():
    # Arrange — patch ChatOpenAI in the intent module, then reload so the
    # module-level _llm is rebuilt against the mock and we can inspect kwargs.
    import agent.workflow.intent as intent_mod

    fake_instance = MagicMock()
    fake_instance.with_structured_output.return_value = MagicMock()

    # reload() re-runs `from langchain_openai import ChatOpenAI`, so patch the
    # name at its source module — patching the already-imported alias would be
    # overwritten by the reload.
    with patch("langchain_openai.ChatOpenAI", return_value=fake_instance) as fake_cls:
        importlib.reload(intent_mod)

    try:
        # Assert — constructor received max_retries == the configured value
        _, kwargs = fake_cls.call_args
        assert kwargs["max_retries"] == settings.llm_max_retries
    finally:
        # Restore the real module so other tests use the genuine instance.
        importlib.reload(intent_mod)
