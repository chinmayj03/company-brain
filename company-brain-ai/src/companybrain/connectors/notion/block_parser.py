"""
B1.4 Notion connector — block parser.

Converts Notion block objects (as returned by the /blocks/{id}/children API)
into plain text suitable for embedding and entity extraction.

Supported block types:
  paragraph, bulleted_list_item, numbered_list_item, to_do, toggle, quote,
  callout, heading_1, heading_2, heading_3, code, divider, table_row.

All other types (image, embed, bookmark, etc.) are silently skipped.
"""
from __future__ import annotations

import re
from typing import Any


def parse_block(block: dict[str, Any]) -> str:
    """
    Convert a single Notion block dict to plain text.

    Returns an empty string for unsupported or unknown block types.
    """
    btype = block.get("type", "")
    data = block.get(btype, {})

    if btype in (
        "paragraph",
        "bulleted_list_item",
        "numbered_list_item",
        "to_do",
        "toggle",
        "quote",
        "callout",
    ):
        return _rich_text_to_str(data.get("rich_text", []))

    if btype in ("heading_1", "heading_2", "heading_3"):
        level = int(btype[-1])
        prefix = "#" * level
        return f"{prefix} {_rich_text_to_str(data.get('rich_text', []))}"

    if btype == "code":
        lang = data.get("language", "")
        code = _rich_text_to_str(data.get("rich_text", []))
        return f"```{lang}\n{code}\n```"

    if btype == "divider":
        return "---"

    if btype == "table_row":
        cells = [_rich_text_to_str(cell) for cell in data.get("cells", [])]
        return " | ".join(cells)

    # image, embed, video, file, pdf, bookmark, link_preview, synced_block, etc.
    return ""


def _rich_text_to_str(rich_text: list[dict]) -> str:
    """Concatenate plain_text fields from a Notion rich_text array."""
    return "".join(rt.get("plain_text", "") for rt in rich_text)


def parse_page_content(blocks: list[dict[str, Any]]) -> str:
    """
    Parse a list of Notion blocks into a single plain-text document.

    Empty or whitespace-only blocks are omitted; non-empty blocks are
    separated by a blank line.
    """
    parts = [parse_block(b) for b in blocks]
    return "\n\n".join(p for p in parts if p.strip())


def extract_entity_mentions(
    text: str,
    known_terms: list[str] | None = None,
) -> list[str]:
    """
    Extract likely entity mentions from parsed Notion text.

    Heuristics:
    - PascalCase identifiers (≥3 chars, e.g. PriorAuth, ClaimProcessor)
    - SCREAMING_SNAKE_CASE tokens (e.g. CLAIM_ID)
    - Any `known_terms` found (case-insensitive substring match)

    Returns a sorted, deduplicated list of strings.
    """
    found: set[str] = set()

    # PascalCase: starts with uppercase, has at least one more uppercase or
    # is a contiguous run of letters ≥3 chars starting with uppercase.
    for m in re.finditer(r"\b[A-Z][a-zA-Z]{2,}(?:[A-Z][a-z]+)*\b", text):
        found.add(m.group())

    # SCREAMING_SNAKE_CASE
    for m in re.finditer(r"\b[A-Z][A-Z0-9_]{2,}\b", text):
        token = m.group()
        if "_" in token or token.isupper():
            found.add(token)

    # Known domain terms (case-insensitive, also matches when words are
    # split by whitespace, e.g. "PriorAuth" matches "prior auth").
    if known_terms:
        lower_text = text.lower()
        # Normalise text for matching: collapse runs of spaces/dashes to nothing
        import re as _re
        collapsed_text = _re.sub(r"[\s\-_]+", "", lower_text)
        for term in known_terms:
            lower_term = term.lower()
            collapsed_term = _re.sub(r"[\s\-_]+", "", lower_term)
            if lower_term in lower_text or collapsed_term in collapsed_text:
                found.add(term)

    return sorted(found)
