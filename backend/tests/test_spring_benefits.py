"""Tests for the spring-type → benefit lookup (agent/workflow/spring_benefits.py).

Key guard: every English spring_type label emitted at ingest
(scripts/ingest.py SPA_QUALITY_MAP values) must have a SPRING_BENEFITS entry, so
a future recommend-time lookup on an onsen's spa_quality_en never silently misses.
"""

from agent.workflow.spring_benefits import SPRING_BENEFITS, benefits_for
from scripts.ingest import SPA_QUALITY_MAP


class TestDriftGuard:
    def test_every_spa_quality_value_has_a_benefit_entry(self):
        # The contract that makes the recommend-time lookup line up exactly.
        for english_label in SPA_QUALITY_MAP.values():
            assert english_label in SPRING_BENEFITS, (
                f"{english_label!r} is a SPA_QUALITY_MAP value with no "
                f"SPRING_BENEFITS entry"
            )

    def test_no_extra_keys_beyond_spa_quality_values(self):
        # Keep the lookup tight to the contract — no orphan keys that can't be hit.
        assert set(SPRING_BENEFITS) == set(SPA_QUALITY_MAP.values())

    def test_all_benefit_values_are_nonempty_strings(self):
        for label, benefit in SPRING_BENEFITS.items():
            assert isinstance(benefit, str) and benefit.strip(), label


class TestBenefitsFor:
    def test_known_key_returns_its_benefit(self):
        assert benefits_for("Simple Spring") == SPRING_BENEFITS["Simple Spring"]

    def test_unknown_key_returns_none(self):
        # e.g. a multi-type or unmapped spa_quality_en string.
        assert benefits_for("Mystery Spring") is None
        assert benefits_for("Simple Spring, Sulfur Spring") is None

    def test_none_returns_none(self):
        assert benefits_for(None) is None
