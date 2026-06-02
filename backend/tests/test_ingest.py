"""Unit tests for the ingest transform helpers.

Regression coverage for the crash where a record with `spa_quality: null`
reached `translate_spa_quality()` and raised
`AttributeError: 'NoneType' object has no attribute 'split'`.
"""

from scripts.ingest import build_document, parse_location, translate_spa_quality


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


class TestBuildDocument:
    def test_prefers_translated_sales_point(self):
        record = {"sales_point": "元の説明", "name": "山田温泉", "prefecture_en": "Okinawa"}
        translation = {"sales_point_en": "A lovely seaside onsen.", "name_en": "Yamada Onsen"}
        assert build_document(record, translation) == "A lovely seaside onsen."

    def test_falls_back_to_original_sales_point_when_translation_missing(self):
        record = {"sales_point": "元の説明", "name": "山田温泉", "prefecture_en": "Okinawa"}
        assert build_document(record, {}) == "元の説明"

    def test_empty_sales_point_falls_back_to_name_and_prefecture(self):
        # Regression: 2 tokai records have sales_point="" — must not embed "".
        record = {"sales_point": "", "name": "山田温泉", "prefecture_en": "Okinawa"}
        translation = {"name_en": "Yamada Onsen", "sales_point_en": ""}
        assert build_document(record, translation) == "Yamada Onsen. Okinawa"

    def test_fallback_uses_original_name_when_name_en_missing(self):
        record = {"sales_point": "", "name": "山田温泉", "prefecture_en": "Okinawa"}
        assert build_document(record, {}) == "山田温泉. Okinawa"

    def test_never_returns_empty_string(self):
        record = {"sales_point": "", "name": "", "prefecture_en": ""}
        assert build_document(record, {}) == "onsen"
