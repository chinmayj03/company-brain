"""
Unit tests for name_matcher — ADR-0093.
"""
import pytest

from companybrain.resolution.name_matcher import names_match, normalize, normalize_title


class TestNormalize:
    def test_lowercase_passthrough(self):
        assert normalize("hello world") == "hello world"

    def test_camel_case_split(self):
        assert normalize("PayerModule") == "payer module"

    def test_pascal_to_tokens(self):
        assert normalize("CrossSourceEntityResolver") == "cross source entity resolver"

    def test_snake_case(self):
        assert normalize("payer_module") == "payer module"

    def test_screaming_snake(self):
        result = normalize("PAYER_MODULE")
        assert result == "payer module"

    def test_kebab_case(self):
        assert normalize("payer-module") == "payer module"

    def test_all_caps_word(self):
        # "API" splits into individual tokens
        result = normalize("APIClient")
        assert "api" in result
        assert "client" in result

    def test_numbers_preserved(self):
        result = normalize("PayerV2")
        assert "payer" in result
        assert "v2" in result or "v" in result  # split behaviour on digit boundary

    def test_dots_as_separator(self):
        assert "payer" in normalize("payer.module")
        assert "module" in normalize("payer.module")

    def test_urn_like_string(self):
        result = normalize("source://notion/page/abc123@workspace")
        assert "source" in result
        assert "notion" in result
        assert "abc123" in result

    def test_empty_string(self):
        assert normalize("") == ""

    def test_whitespace_only(self):
        assert normalize("   ") == ""


class TestNormalizeTitle:
    def test_collapses_extra_whitespace(self):
        assert normalize_title("  payer   module  ") == "payer module"

    def test_camel_case(self):
        assert normalize_title("PayerModule") == "payer module"

    def test_already_normalized(self):
        assert normalize_title("payer module") == "payer module"


class TestNamesMatch:
    # ── Positive cases ────────────────────────────────────────────────────────

    def test_identical_titles(self):
        assert names_match("Payer Module", "Payer Module") is True

    def test_camel_vs_space(self):
        assert names_match("PayerModule", "payer module") is True

    def test_pascal_vs_snake(self):
        assert names_match("PayerModule", "payer_module") is True

    def test_snake_vs_kebab(self):
        assert names_match("payer_module", "payer-module") is True

    def test_mixed_case(self):
        assert names_match("PAYER MODULE", "payer module") is True

    def test_cross_source_entity_resolver(self):
        assert names_match(
            "CrossSourceEntityResolver",
            "cross source entity resolver",
        ) is True

    def test_title_with_extra_whitespace(self):
        assert names_match("Payer  Module", "payer module") is True

    # ── Negative cases ────────────────────────────────────────────────────────

    def test_different_names(self):
        assert names_match("Payer Module", "Order Service") is False

    def test_subset_not_match(self):
        assert names_match("Payer", "Payer Module") is False

    def test_similar_but_different(self):
        assert names_match("PayerModule", "PaymentModule") is False

    def test_empty_vs_nonempty(self):
        assert names_match("", "payer module") is False

    def test_both_empty(self):
        # Both empty → both normalize to "" → they match (degenerate case)
        assert names_match("", "") is True
