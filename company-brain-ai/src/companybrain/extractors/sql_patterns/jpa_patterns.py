"""JPA / Spring Data @Query pattern matcher.

Handles:
  @Query("SELECT u FROM User u WHERE u.id = :id")
  @Query(value = "SELECT …", nativeQuery = true)
  @NamedQuery(name = "…", query = "SELECT …")
  @NamedNativeQuery(name = "…", query = "SELECT …")

Returns: list[RawMatch] — one entry per found SQL string.

Confidence tiers:
  - JPQL / named queries that use :param or ?1 syntax → prepared_statement
  - nativeQuery=true with ?n params                   → prepared_statement
  - Any other literal                                  → literal_string
"""
from __future__ import annotations

import re
from typing import Iterator

from companybrain.extractors.sql_patterns import RawMatch
from companybrain.extractors.sql_deep import (
    TIER_LITERAL_STRING,
    TIER_PREPARED_STATEMENT,
)

# Matches single- or double-quoted string literal that may span lines.
# We capture the inner text and the opening quote character.
_STRING_BODY_RE = re.compile(
    r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'',
    re.DOTALL,
)

# @Query, @NamedQuery, @NamedNativeQuery annotations
_QUERY_ANNOTATION_RE = re.compile(
    r'@(?:Named)?(?:Native)?Query\b',
)

# value attribute or positional first arg
_VALUE_ATTR_RE = re.compile(
    r'(?:value\s*=\s*)?' + r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\''
    , re.DOTALL,
)

# nativeQuery flag
_NATIVE_RE = re.compile(r'nativeQuery\s*=\s*true', re.IGNORECASE)

# named :param or ?1 positional
_PLACEHOLDER_RE = re.compile(r'\?\d*|\:[a-zA-Z_]\w*')


def extract(content: str) -> list[RawMatch]:
    """Extract all @Query / @NamedQuery SQL strings from Java *content*."""
    results: list[RawMatch] = []
    lines = content.split("\n")

    # Fast path: only scan if any @Query annotation is present.
    if not _QUERY_ANNOTATION_RE.search(content):
        return results

    for match in _QUERY_ANNOTATION_RE.finditer(content):
        ann_start = match.start()
        line_no = content.count("\n", 0, ann_start) + 1

        # Find the opening paren of the annotation body.
        paren_pos = content.find("(", ann_start)
        if paren_pos < 0:
            continue

        # Extract the annotation body up to the matching close paren.
        body, end_pos = _grab_paren_body(content, paren_pos)
        if not body:
            continue

        is_native = bool(_NATIVE_RE.search(body))

        # Find all string literals inside the annotation body — the first one
        # (or the one following "value =") is the SQL string.
        sql_text = _first_string_literal(body)
        if not sql_text:
            continue

        # Clean up concatenated strings (e.g. multiline @Query)
        sql_text = _unwrap_concat(sql_text, body)
        if not sql_text.strip():
            continue

        tier = TIER_PREPARED_STATEMENT if _PLACEHOLDER_RE.search(sql_text) else TIER_LITERAL_STRING
        label = "@NativeQuery" if is_native else "@Query"
        results.append(RawMatch(sql_text=sql_text.strip(), line_no=line_no, tier=tier, pattern_type=label))

    return results


def _grab_paren_body(content: str, open_idx: int) -> tuple[str, int]:
    """Return the content between matching parens starting at open_idx.

    Handles Java text blocks (\"\"\"…\"\"\") which may contain unbalanced parens.
    """
    depth = 0
    in_str: str | None = None
    in_text_block = False
    start = open_idx + 1
    i = open_idx
    while i < len(content):
        ch = content[i]
        # Handle Java text block: """…"""
        if not in_str and not in_text_block and content[i:i+3] == '"""':
            in_text_block = True
            i += 3
            continue
        if in_text_block:
            if content[i:i+3] == '"""':
                in_text_block = False
                i += 3
                continue
            i += 1
            continue
        if in_str:
            if ch == "\\" and i + 1 < len(content):
                i += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in ('"', "'"):
            in_str = ch
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return content[start:i], i
        i += 1
    return "", -1


def _first_string_literal(body: str) -> str:
    """Return the text of the first string literal in *body*.

    Handles both regular string literals and Java text blocks (\"\"\"…\"\"\").
    """
    # Prefer "value = <literal>" or "query = <literal>" — check text blocks first
    for attr in ("value", "query"):
        # Java text block: value = """…"""
        m = re.search(rf'{attr}\s*=\s*"""(.*?)"""', body, re.DOTALL)
        if m:
            return _dedent_text_block(m.group(1))
        m = re.search(rf'{attr}\s*=\s*"((?:[^"\\]|\\.)*)"', body, re.DOTALL)
        if m:
            return m.group(1)
        m = re.search(rf"{attr}\s*=\s*'((?:[^'\\]|\\.)*)'", body, re.DOTALL)
        if m:
            return m.group(1)

    # Check for bare Java text block first (positional arg).
    m = re.search(r'"""\s*(.*?)"""', body, re.DOTALL)
    if m:
        return _dedent_text_block(m.group(1))

    # Fall back to first regular string literal.
    m = _STRING_BODY_RE.search(body)
    if m:
        return m.group(1) or m.group(2) or ""
    return ""


def _dedent_text_block(text: str) -> str:
    """Strip common leading whitespace from a Java text block."""
    lines = text.split("\n")
    # Remove leading empty line (Java text blocks start with newline after opening """)
    if lines and not lines[0].strip():
        lines = lines[1:]
    # Remove trailing empty/whitespace-only line
    if lines and not lines[-1].strip():
        lines = lines[:-1]
    # Find minimum indentation of non-empty lines
    min_indent = float("inf")
    for ln in lines:
        if ln.strip():
            indent = len(ln) - len(ln.lstrip())
            min_indent = min(min_indent, indent)
    if min_indent == float("inf"):
        min_indent = 0
    return "\n".join(ln[int(min_indent):] for ln in lines)


def _unwrap_concat(sql_text: str, full_body: str) -> str:
    """If the annotation uses string concatenation (multi-line queries), stitch them together."""
    # Look for + "…" patterns following the first literal in the annotation body.
    # This is a best-effort heuristic — it only handles simple adjacent string concat.
    parts = [sql_text]
    # Find continuation: + "next part"
    concat_re = re.compile(r'\+\s*"((?:[^"\\]|\\.)*)"', re.DOTALL)
    for m in concat_re.finditer(full_body):
        parts.append(m.group(1))
    return " ".join(parts)
