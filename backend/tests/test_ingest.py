"""Unit tests for the ingest transform helpers.

Regression coverage for the crash where a record with `spa_quality: null`
reached `translate_spa_quality()` and raised
`AttributeError: 'NoneType' object has no attribute 'split'`.
"""

from scripts.ingest import parse_location, translate_spa_quality


class TestTranslateSpaQuality:
    def test_none_returns_empty_string(self):
        # Regression: real tokai data has a record with spa_quality=null.
        assert translate_spa_quality(None) == ""

    def test_empty_string_returns_empty_string(self):
        assert translate_spa_quality("") == ""

    def test_single_known_quality_is_translated(self):
        assert translate_spa_quality("単純温泉") == "Simple Spring"

    def test_multiple_qualities_split_on_japanese_comma(self):
        assert translate_spa_quality("単純温泉、硫黄泉") == "Simple Spring, Sulfur Spring"

    def test_unknown_quality_passes_through(self):
        assert translate_spa_quality("謎の泉") == "謎の泉"


class TestParseLocation:
    def test_none_returns_empty_pair(self):
        assert parse_location(None) == ("", "")

    def test_empty_string_returns_empty_pair(self):
        assert parse_location("") == ("", "")

    def test_prefecture_and_city_split_on_first_space(self):
        assert parse_location("沖縄県 那覇市") == ("沖縄県", "那覇市")

    def test_prefecture_only(self):
        assert parse_location("沖縄県") == ("沖縄県", "")
