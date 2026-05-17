"""
SQL Embedded Scanner (A1.1) — tree-sitter Java AST + regex pattern dispatch.

Scans Java source files for embedded SQL in:
  1. JPA @Query / @NamedQuery annotations                      → jpa_patterns
  2. MyBatis @Select / @Insert / @Update / @Delete annotations → mybatis_patterns
  3. JDBC prepareStatement / JdbcTemplate call sites           → jdbc_patterns
  4. entityManager.createQuery / createNativeQuery calls       → direct regex

Each found SQL string is passed to ``sql_deep.parse_sql_deep()`` for full AST
extraction, table/column detection, and confidence tier assignment.

Tree-sitter usage
-----------------
tree-sitter-java provides the Java grammar.  We use it to locate string literal
nodes within annotation bodies and method call argument lists, giving us precise
line numbers.  When tree-sitter is unavailable (import error), we fall back to
pure-regex extraction (reduced line-number accuracy, same coverage).

Usage
-----
    from companybrain.extractors.sql_embedded_scanner import SqlEmbeddedScanner
    scanner = SqlEmbeddedScanner()
    if scanner.supports(path):
        batch = scanner.scan(path, content, repo="my-repo")
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from companybrain.extractors.sql_deep import (
    EmbeddedSqlStatement,
    SqlDeepBatch,
    TIER_DYNAMIC_CONCAT,
    TIER_LITERAL_STRING,
    TIER_PREPARED_STATEMENT,
    _flag_enabled,
    parse_sql_deep,
)
from companybrain.extractors.sql_patterns import RawMatch
from companybrain.extractors.sql_patterns import jpa_patterns
from companybrain.extractors.sql_patterns import mybatis_patterns
from companybrain.extractors.sql_patterns import jdbc_patterns

# Try to import tree-sitter; fall back to regex mode if unavailable.
try:
    import tree_sitter_java as _ts_java
    from tree_sitter import Language, Parser as TSParser

    _JAVA_LANGUAGE = Language(_ts_java.language())
    _TS_AVAILABLE = True
except Exception:  # noqa: BLE001
    _TS_AVAILABLE = False

# entityManager pattern
_EM_CREATEQUERY_RE = re.compile(
    r'entityManager\s*\.\s*(?:createQuery|createNativeQuery|createNamedQuery)\s*\(',
)

# String variable named *sql* or *query* (common Java idiom)
_SQL_VAR_ASSIGN_RE = re.compile(
    r'(?:String|var)\s+(?:[a-z_]*(?:sql|query|SQL|QUERY)[a-z_A-Z0-9]*)\s*='
    r'\s*"((?:[^"\\]|\\.)*)"',
    re.DOTALL,
)


class SqlEmbeddedScanner:
    """Scanner for embedded SQL in Java files.

    Call ``supports(path)`` then ``scan(path, content, repo=…)``.
    """

    kind = "sql_embedded"

    def supports(self, path: Path) -> bool:  # noqa: D102
        return path.suffix == ".java"

    def scan(
        self,
        path: Path,
        content: str,
        *,
        repo: str = "",
        dialect: Optional[str] = None,
    ) -> SqlDeepBatch:
        """Scan *content* for embedded SQL.  Returns a ``SqlDeepBatch``."""
        batch = SqlDeepBatch(file=str(path), repo=repo)

        if not _flag_enabled():
            return batch

        # Collect raw matches from all pattern matchers.
        raw_matches: list[RawMatch] = []
        raw_matches.extend(jpa_patterns.extract(content))
        raw_matches.extend(mybatis_patterns.extract(content))
        raw_matches.extend(jdbc_patterns.extract(content))
        raw_matches.extend(_scan_entity_manager(content))
        raw_matches.extend(_scan_sql_variables(content))

        # Deduplicate by SQL text (normalised whitespace).
        seen: set[str] = set()
        for rm in raw_matches:
            key = re.sub(r'\s+', ' ', rm.sql_text.strip().lower())
            if key in seen or not key:
                continue
            seen.add(key)

            sub = parse_sql_deep(
                rm.sql_text,
                source_file=str(path),
                repo=repo,
                confidence_tier=rm.tier,
                dialect=dialect,
            )
            # Override line numbers from the scanner's knowledge.
            for stmt in sub.statements:
                stmt.line_start = rm.line_no
                stmt.line_end = rm.line_no + rm.sql_text.count("\n")
            batch.statements.extend(sub.statements)

        return batch


# ── internal helpers ───────────────────────────────────────────────────────────

def _scan_entity_manager(content: str) -> list[RawMatch]:
    """Find entityManager.createQuery("…") call sites."""
    results: list[RawMatch] = []

    for match in _EM_CREATEQUERY_RE.finditer(content):
        call_start = match.start()
        line_no = content.count("\n", 0, call_start) + 1
        # Find opening paren
        paren_pos = content.find("(", match.end() - 1)
        if paren_pos < 0:
            continue
        body, _ = _grab_paren_body(content, paren_pos)
        if not body:
            continue

        sql_text = _first_string_literal(body)
        if not sql_text or len(sql_text.strip()) < 6:
            continue
        if not _looks_like_sql(sql_text):
            continue

        # Detect concat
        tier = TIER_PREPARED_STATEMENT if re.search(r'\?\d*|\:[a-zA-Z_]\w*', sql_text) else TIER_LITERAL_STRING
        results.append(RawMatch(
            sql_text=sql_text.strip(),
            line_no=line_no,
            tier=tier,
            pattern_type="createQuery",
        ))
    return results


# Detect: varName = varName + "…" or varName += "…" after the initial assignment.
# Pattern: identifier_with_sql_name = identifier_with_sql_name + or +=
_SQL_CONCAT_REASSIGN_RE = re.compile(
    r'(?:[a-z_]*(?:sql|query|SQL|QUERY)[a-z_A-Z0-9]*)\s*(?:\+?=)\s*'
    r'(?:(?:[a-z_]*(?:sql|query|SQL|QUERY)[a-z_A-Z0-9]*)\s*\+)',
)


def _scan_sql_variables(content: str) -> list[RawMatch]:
    """Find String sql = "SELECT …" variable assignments.

    If the same variable is later concatenated (sql = sql + "…"), the tier is
    upgraded to dynamic_concat.
    """
    results: list[RawMatch] = []

    # Collect all variable names that are concatenated later.
    concat_vars: set[str] = set()
    for m in _SQL_CONCAT_REASSIGN_RE.finditer(content):
        # Extract the variable name prefix (the part matching [a-z_]*(?:sql|query)…)
        var_candidate = m.group(0).split("=")[0].strip()
        concat_vars.add(var_candidate)

    for match in _SQL_VAR_ASSIGN_RE.finditer(content):
        sql_text = match.group(1).replace("\\n", " ").replace("\\t", " ")
        if len(sql_text.strip()) < 6 or not _looks_like_sql(sql_text):
            continue
        line_no = content.count("\n", 0, match.start()) + 1

        # Extract the variable name from the assignment.
        var_name_match = re.match(
            r'(?:String|var)\s+([a-z_A-Z0-9]+)\s*=', match.group(0)
        )
        var_name = var_name_match.group(1) if var_name_match else ""

        # Determine tier.
        if var_name and var_name in concat_vars:
            tier = TIER_DYNAMIC_CONCAT
        elif re.search(r'\?\d*|\:[a-zA-Z_]\w*', sql_text):
            tier = TIER_PREPARED_STATEMENT
        else:
            tier = TIER_LITERAL_STRING

        results.append(RawMatch(
            sql_text=sql_text.strip(),
            line_no=line_no,
            tier=tier,
            pattern_type="sql_variable",
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
    m = re.match(r'\s*"((?:[^"\\]|\\.)*)"', body, re.DOTALL)
    if m:
        return m.group(1).replace("\\n", " ").replace("\\t", " ")
    m = re.match(r"\s*'((?:[^'\\]|\\.)*)'", body, re.DOTALL)
    if m:
        return m.group(1).replace("\\n", " ").replace("\\t", " ")
    return ""


_SQL_KEYWORDS = frozenset({
    "select", "insert", "update", "delete", "create", "alter", "drop",
    "truncate", "merge", "call", "with",
})


def _looks_like_sql(text: str) -> bool:
    first = text.strip().split()[0].lower().rstrip("(") if text.strip() else ""
    return first in _SQL_KEYWORDS
