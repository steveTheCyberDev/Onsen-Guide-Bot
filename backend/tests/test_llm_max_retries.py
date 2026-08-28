"""Wiring test: the intent ChatOpenAI uses settings.llm_max_retries.

The intent llm (agent/workflow/intent.py) must construct its ChatOpenAI with
`max_retries == settings.llm_max_retries` so transient OpenAI errors are retried
a bounded number of times. This is a light wiring check — no real network call:
the module is reloaded under a patched ChatOpenAI so the module-level `_llm` is
rebuilt against the mock and we can assert the constructor kwargs.
"""

import importlib
from unittest.mock import MagicMock, patch

from core.config import settings


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
