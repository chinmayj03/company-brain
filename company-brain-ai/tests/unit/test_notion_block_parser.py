"""
Unit tests for companybrain.connectors.notion.block_parser.

All tests are pure Python — no network, no LLM calls.
"""
from __future__ import annotations

import pytest

from companybrain.connectors.notion.block_parser import (
    parse_block,
    parse_page_content,
    extract_entity_mentions,
    _rich_text_to_str,
)


# ── _rich_text_to_str ───────────────────────────────────────────────────────

class TestRichTextToStr:
    def test_empty_list(self):
        assert _rich_text_to_str([]) == ""

    def test_single_element(self):
        assert _rich_text_to_str([{"plain_text": "Hello"}]) == "Hello"

    def test_multiple_elements(self):
        rt = [{"plain_text": "Hello"}, {"plain_text": " "}, {"plain_text": "World"}]
        assert _rich_text_to_str(rt) == "Hello World"

    def test_missing_plain_text_key(self):
        # Non-text rich_text objects (e.g. mentions) may lack plain_text
        rt = [{"type": "mention"}, {"plain_text": "foo"}]
        assert _rich_text_to_str(rt) == "foo"


# ── parse_block ─────────────────────────────────────────────────────────────

class TestParseBlock:
    def _rt(self, text: str) -> list[dict]:
        return [{"plain_text": text}]

    # --- paragraph ---
    def test_paragraph(self):
        block = {"type": "paragraph", "paragraph": {"rich_text": self._rt("Hello")}}
        assert parse_block(block) == "Hello"

    def test_paragraph_empty(self):
        block = {"type": "paragraph", "paragraph": {"rich_text": []}}
        assert parse_block(block) == ""

    # --- list items ---
    def test_bulleted_list_item(self):
        block = {
            "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": self._rt("item one")},
        }
        assert parse_block(block) == "item one"

    def test_numbered_list_item(self):
        block = {
            "type": "numbered_list_item",
            "numbered_list_item": {"rich_text": self._rt("step 1")},
        }
        assert parse_block(block) == "step 1"

    def test_to_do(self):
        block = {"type": "to_do", "to_do": {"rich_text": self._rt("Buy milk")}}
        assert parse_block(block) == "Buy milk"

    def test_toggle(self):
        block = {"type": "toggle", "toggle": {"rich_text": self._rt("Expand me")}}
        assert parse_block(block) == "Expand me"

    def test_quote(self):
        block = {"type": "quote", "quote": {"rich_text": self._rt("Wise words")}}
        assert parse_block(block) == "Wise words"

    def test_callout(self):
        block = {"type": "callout", "callout": {"rich_text": self._rt("Note!")}}
        assert parse_block(block) == "Note!"

    # --- headings ---
    def test_heading_1(self):
        block = {"type": "heading_1", "heading_1": {"rich_text": self._rt("Big Title")}}
        assert parse_block(block) == "# Big Title"

    def test_heading_2(self):
        block = {"type": "heading_2", "heading_2": {"rich_text": self._rt("Title")}}
        assert parse_block(block) == "## Title"

    def test_heading_3(self):
        block = {"type": "heading_3", "heading_3": {"rich_text": self._rt("Sub")}}
        assert parse_block(block) == "### Sub"

    # --- code ---
    def test_code_with_language(self):
        block = {
            "type": "code",
            "code": {
                "language": "python",
                "rich_text": self._rt("print('hi')"),
            },
        }
        result = parse_block(block)
        assert result.startswith("```python")
        assert "print('hi')" in result
        assert result.endswith("```")

    def test_code_no_language(self):
        block = {
            "type": "code",
            "code": {"language": "", "rich_text": self._rt("code")},
        }
        assert parse_block(block) == "```\ncode\n```"

    # --- divider ---
    def test_divider(self):
        block = {"type": "divider", "divider": {}}
        assert parse_block(block) == "---"

    # --- table_row ---
    def test_table_row(self):
        block = {
            "type": "table_row",
            "table_row": {
                "cells": [
                    [{"plain_text": "Name"}],
                    [{"plain_text": "Age"}],
                ]
            },
        }
        assert parse_block(block) == "Name | Age"

    def test_table_row_empty_cells(self):
        block = {"type": "table_row", "table_row": {"cells": []}}
        assert parse_block(block) == ""

    # --- unsupported types return empty string ---
    def test_image_returns_empty(self):
        block = {"type": "image", "image": {"type": "external", "external": {"url": "x"}}}
        assert parse_block(block) == ""

    def test_embed_returns_empty(self):
        block = {"type": "embed", "embed": {"url": "https://example.com"}}
        assert parse_block(block) == ""

    def test_unknown_type_returns_empty(self):
        block = {"type": "spaceship", "spaceship": {}}
        assert parse_block(block) == ""

    def test_missing_type_returns_empty(self):
        assert parse_block({}) == ""


# ── parse_page_content ──────────────────────────────────────────────────────

class TestParsePageContent:
    def _para(self, text: str) -> dict:
        return {
            "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": text}]},
        }

    def _heading(self, level: int, text: str) -> dict:
        key = f"heading_{level}"
        return {
            "type": key,
            key: {"rich_text": [{"plain_text": text}]},
        }

    def test_empty_list(self):
        assert parse_page_content([]) == ""

    def test_single_paragraph(self):
        assert parse_page_content([self._para("Hello")]) == "Hello"

    def test_multiple_blocks_joined(self):
        blocks = [self._para("First"), self._para("Second")]
        result = parse_page_content(blocks)
        assert "First" in result
        assert "Second" in result
        assert result.index("First") < result.index("Second")

    def test_whitespace_only_blocks_omitted(self):
        blocks = [self._para("Hello"), self._para("   "), self._para("World")]
        result = parse_page_content(blocks)
        assert "Hello" in result
        assert "World" in result
        # The whitespace-only block should not contribute extra blank lines
        assert result.strip().count("\n\n") == 1  # exactly one separator

    def test_mixed_types(self):
        blocks = [
            self._heading(2, "Section"),
            self._para("Content here"),
            {"type": "image", "image": {}},  # should be skipped
        ]
        result = parse_page_content(blocks)
        assert "## Section" in result
        assert "Content here" in result

    def test_returns_string(self):
        assert isinstance(parse_page_content([self._para("x")]), str)


# ── extract_entity_mentions ─────────────────────────────────────────────────

class TestExtractEntityMentions:
    def test_pascal_case_detected(self):
        mentions = extract_entity_mentions("PriorAuth and Aetna are payers")
        assert "PriorAuth" in mentions
        assert "Aetna" in mentions

    def test_known_terms_matched_case_insensitive(self):
        mentions = extract_entity_mentions(
            "we use prior auth here",
            known_terms=["PriorAuth"],
        )
        assert "PriorAuth" in mentions

    def test_known_terms_not_in_text(self):
        mentions = extract_entity_mentions(
            "nothing relevant",
            known_terms=["PriorAuth"],
        )
        assert "PriorAuth" not in mentions

    def test_returns_sorted_list(self):
        mentions = extract_entity_mentions("Zebra Apple Mango")
        assert mentions == sorted(mentions)

    def test_deduplication(self):
        mentions = extract_entity_mentions("Aetna and Aetna again")
        assert mentions.count("Aetna") == 1

    def test_short_words_ignored(self):
        # Single and two-letter PascalCase tokens should NOT be captured
        mentions = extract_entity_mentions("An AI system")
        # "An" is 2 chars — not captured; "AI" is SCREAMING but only 2 chars
        for m in mentions:
            assert len(m) >= 3

    def test_empty_text(self):
        assert extract_entity_mentions("") == []

    def test_no_mentions(self):
        assert extract_entity_mentions("just lowercase text here") == []

    def test_screaming_snake_case(self):
        mentions = extract_entity_mentions("The CLAIM_ID field")
        assert "CLAIM_ID" in mentions
