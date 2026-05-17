"""MyBatis annotation pattern matcher.

Handles Java MyBatis 3 annotations:
  @Select("SELECT …")
  @Insert("INSERT INTO …")
  @Update("UPDATE …")
  @Delete("DELETE FROM …")
  @SelectProvider, @InsertProvider, etc. — not extracted (dynamic; low-value)

Also handles multi-string array form:
  @Select({"SELECT a, b", "FROM foo", "WHERE id = #{id}"})

Confidence tiers:
  - #{param} / @{param} style → prepared_statement
  - Plain literal            → literal_string
"""
from __future__ import annotations

import re

from companybrain.extractors.sql_patterns import RawMatch
from companybrain.extractors.sql_deep import (
    TIER_LITERAL_STRING,
    TIER_PREPARED_STATEMENT,
)

_MYBATIS_ANNOTATION_RE = re.compile(
    r'@(Select|Insert|Update|Delete)\s*\(',
    re.IGNORECASE,
)

# MyBatis parameter marker: #{id} or ${column}
_MYBATIS_PARAM_RE = re.compile(r'#\{[^}]+\}|\$\{[^}]+\}')


def extract(content: str) -> list[RawMatch]:
    """Extract all MyBatis @Select / @Insert / @Update / @Delete SQL strings."""
    results: list[RawMatch] = []

    if not _MYBATIS_ANNOTATION_RE.search(content):
        return results

    for match in _MYBATIS_ANNOTATION_RE.finditer(content):
        ann_name = match.group(1).upper()
        ann_start = match.start()
        line_no = content.count("\n", 0, ann_start) + 1

        paren_pos = match.end() - 1  # the "(" captured in the pattern
        body, _ = _grab_paren_body(content, paren_pos)
        if not body:
            continue

        sql_text = _extract_sql_from_body(body)
        if not sql_text.strip():
            continue

        tier = TIER_PREPARED_STATEMENT if _MYBATIS_PARAM_RE.search(sql_text) else TIER_LITERAL_STRING
        results.append(RawMatch(
            sql_text=sql_text.strip(),
            line_no=line_no,
            tier=tier,
            pattern_type=f"@{ann_name.capitalize()}",
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


def _extract_sql_from_body(body: str) -> str:
    """Extract SQL text from annotation body, handling both single string and array forms."""
    body = body.strip()

    # Array form: {"SELECT a", "FROM b", ...}
    if body.startswith("{"):
        inner = body[1:body.rfind("}")]
        parts: list[str] = []
        for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', inner, re.DOTALL):
            parts.append(m.group(1).replace("\\n", " ").replace("\\t", " "))
        return " ".join(parts).strip()

    # Single string form: "SELECT …"
    m = re.match(r'^"((?:[^"\\]|\\.)*)"', body, re.DOTALL)
    if m:
        return m.group(1).replace("\\n", " ").replace("\\t", " ")

    m = re.match(r"^'((?:[^'\\]|\\.)*)'", body, re.DOTALL)
    if m:
        return m.group(1).replace("\\n", " ").replace("\\t", " ")

    return ""
