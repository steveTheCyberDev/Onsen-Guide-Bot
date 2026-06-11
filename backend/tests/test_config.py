"""Tests for the new V2.5 ask-mode / KB config knobs in core.config.

These assert the safe local-friendly defaults (so prod behaviour is unchanged
until an env var flips them) and the kb_data_dir env-split property, mirroring
the data_dir pattern.
"""

from pathlib import Path
from unittest.mock import patch

from core.config import settings


def test_kb_collection_default():
    assert settings.kb_collection == "onsen_knowledge"


def test_kb_data_path_default_empty():
    # "" → "use the computed local default" (data_dir / knowledge).
    assert settings.kb_data_path == ""


def test_ask_enabled_defaults_off():
    # Gate must default False so ask returns the safe stub until flipped.
    assert settings.ask_enabled is False


def test_ask_top_k_default():
    assert settings.ask_top_k == 4


def test_ask_max_distance_default():
    assert settings.ask_max_distance == 0.85


def test_ask_model_default_empty_for_callsite_fallback():
    # "" means the call site falls back to intent_model (don't resolve here).
    assert settings.ask_model == ""


def test_kb_data_dir_uses_explicit_path_when_set():
    # Arrange — KB_DATA_PATH set (prod override, e.g. /app/data/knowledge).
    with patch.object(settings, "kb_data_path", "/app/data/knowledge"):
        # Assert
        assert settings.kb_data_dir == Path("/app/data/knowledge")


def test_kb_data_dir_falls_back_to_data_dir_knowledge_when_unset():
    # Arrange — unset KB_DATA_PATH resolves to data_dir / "knowledge".
    with patch.object(settings, "kb_data_path", ""):
        # Assert
        assert settings.kb_data_dir == settings.data_dir / "knowledge"
