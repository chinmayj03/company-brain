"""JDBC PreparedStatement / Statement pattern matcher.

Handles:
  conn.prepareStatement("SELECT …")
  conn.prepareCall("CALL …")
  stmt.executeQuery("SELECT …")
  stmt.executeUpdate("INSERT INTO …")
  stmt.execute("…")
  jdbcTemplate.query("SELECT …", …)
  jdbcTemplate.queryForObject("SELECT …", …)
  jdbcTemplate.update("INSERT …", …)
  namedParameterJdbcTemplate.query("SELECT …", …)

Confidence tiers:
  - ? placeholder present   → prepared_statement
  - :namedParam present      → prepared_statement
  - No placeholders          → literal_string
  - Detects string concat (+) → dynamic_concat
"""
from __future__ import annotations

import re

from companybrain.extractors.sql_patterns import RawMatch
from companybrain.extractors.sql_deep import (
    TIER_DYNAMIC_CONCAT,
    TIER_LITERAL_STRING,
    TIER_PREPARED_STATEMENT,
)

# JDBC + Spring JdbcTemplate method names
_JDBC_CALL_RE = re.compile(
    r'\b(?:prepareStatement|prepareCall|executeQuery|executeUpdate|execute'
    r'|query|queryForObject|queryForList|queryForMap|queryForRowSet'
    r'|update|batchUpdate)\s*\(',
)

# Detects trailing + "…" or + variable after the string literal (dynamic concat)
_CONCAT_AFTER_RE = re.compile(r'"\s*\+')

_PLACEHOLDER_RE = re.compile(r'\?|\:[a-zA-Z_]\w*')


def extract(content: str) -> list[RawMatch]:
    """Extract SQL string literals from JDBC / JdbcTemplate call sites."""
    results: list[RawMatch] = []

    if not _JDBC_CALL_RE.search(content):
        return results

    for match in _JDBC_CALL_RE.finditer(content):
        call_start = match.start()
        line_no = content.count("\n", 0, call_start) + 1

        # Find the opening paren
        paren_pos = content.find("(", match.end() - 1)
        if paren_pos < 0:
            continue

        body, _ = _grab_paren_body(content, paren_pos)
        if not body:
            continue

        # The first argument should be a string literal.
        sql_text = _first_string_literal(body)
        if not sql_text:
            continue

        # Skip non-SQL looking strings (very short, or clearly not SQL).
        if len(sql_text.strip()) < 6:
            continue
        if not _looks_like_sql(sql_text):
            continue

        # Detect dynamic concat.
        if _CONCAT_AFTER_RE.search(body):
            tier = TIER_DYNAMIC_CONCAT
        elif _PLACEHOLDER_RE.search(sql_text):
            tier = TIER_PREPARED_STATEMENT
        else:
            tier = TIER_LITERAL_STRING

        results.append(RawMatch(
            sql_text=sql_text.strip(),
            line_no=line_no,
            tier=tier,
            pattern_type=match.group(0).rstrip("(").strip(),
        ))

    return results


def _grab_paren_body(content: str, open_idx: int) -> tuple[str, int]:
    depth = 0
    in_str: str | None = None
    start = open_idx + 1
    i = open_idx
    while i < len(content):
        ch = content[i]
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
    """Return text of the first string literal in *body*."""
    m = re.match(r'\s*"((?:[^"\\]|\\.)*)"', body, re.DOTALL)
    if m:
        return m.group(1).replace("\\n", " ").replace("\\t", " ")
    m = re.match(r"\s*'((?:[^'\\]|\\.)*)'", body, re.DOTALL)
    if m:
        return m.group(1).replace("\\n", " ").replace("\\t", " ")
    return ""


_SQL_KEYWORDS = frozenset({
    "select", "insert", "update", "delete", "create", "alter", "drop",
    "truncate", "merge", "call", "with", "from", "where", "set",
})


def _looks_like_sql(text: str) -> bool:
    """Heuristic: does the text start with a known SQL keyword?"""
    first = text.strip().split()[0].lower().rstrip("(") if text.strip() else ""
    return first in _SQL_KEYWORDS
