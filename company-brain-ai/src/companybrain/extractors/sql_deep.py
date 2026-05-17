"""
SQL Deep Extractor (A1.1) — sqlglot-based DDL + DML + DQL parser.

Replaces the DDL-only path in ``schema_sql.py`` when the feature flag
``SQL_DEEP_EXTRACTOR_ENABLED`` is true (default). The legacy path remains
available behind the flag.

Design choices
--------------
* sqlglot is used as the sole SQL AST engine — it handles 20+ dialects including
  ANSI, Postgres, MySQL, T-SQL, BigQuery, SQLite and JPQL-compatible constructs.
* Column-level lineage is extracted via ``sqlglot.lineage`` on parseable SELECT
  statements (best-effort; skipped on parse failure).
* Confidence tiers mirror the extraction context, not SQL content:
  - ``literal_string``           — full SQL in one string literal (e.g. raw .sql file)
  - ``prepared_statement``       — SQL with ? or :name placeholders
  - ``dynamic_concat``           — SQL built via concatenation (low confidence)
* This module is intentionally thin. JPA / JDBC / MyBatis pattern matching lives
  in ``sql_patterns/`` and the tree-sitter scanner lives in ``sql_embedded_scanner``.
* The feature flag is checked once per ``extract()`` call; set via env-var
  ``SQL_DEEP_EXTRACTOR_ENABLED`` or the ``config.py`` ``Settings`` object.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import sqlglot
    import sqlglot.expressions as exp
    _SQLGLOT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _SQLGLOT_AVAILABLE = False

# Lazy import to avoid circular dependency:
# schema_sql → base → extractors/__init__ → schema_sql (circular).
# We import SchemaSqlExtractor only at call-time inside SqlDeepExtractor.
from companybrain.models.entities import ExtractedBatch

# ── confidence tiers ───────────────────────────────────────────────────────────

TIER_LITERAL_STRING = "literal_string"
TIER_PREPARED_STATEMENT = "prepared_statement"
TIER_DYNAMIC_CONCAT = "dynamic_concat"

# ── data model ─────────────────────────────────────────────────────────────────


@dataclass
class EmbeddedSqlStatement:
    """A single SQL statement extracted from any source (raw .sql, Java, Python, …).

    Attributes
    ----------
    raw_sql:         The original SQL text (may contain placeholders).
    stmt_type:       Uppercase keyword: SELECT / INSERT / UPDATE / DELETE / CREATE / ALTER / DROP / OTHER.
    tables:          Tables referenced (source + target, deduplicated, lowercase).
    columns:         Columns referenced in the statement (best-effort).
    confidence_tier: One of the TIER_* constants defined in this module.
    line_start:      1-based line number in the source file (0 = unknown).
    line_end:        Inclusive end line (0 = unknown).
    source_file:     Path of the file this SQL came from.
    repo:            Repository name (for URN generation).
    lineage:         Dict mapping output_column → {source_table.source_column, …}. May be empty.
    parse_error:     Non-empty when sqlglot failed to parse; raw text still captured.
    """
    raw_sql: str
    stmt_type: str
    tables: list[str]
    columns: list[str]
    confidence_tier: str
    line_start: int = 0
    line_end: int = 0
    source_file: str = ""
    repo: str = ""
    lineage: dict[str, set[str]] = field(default_factory=dict)
    parse_error: str = ""


@dataclass
class SqlDeepBatch:
    """Aggregated results from a single file extraction."""
    file: str
    repo: str
    statements: list[EmbeddedSqlStatement] = field(default_factory=list)

    def add(self, stmt: EmbeddedSqlStatement) -> None:
        self.statements.append(stmt)

    @property
    def coverage_count(self) -> int:
        return len(self.statements)


# ── feature flag ───────────────────────────────────────────────────────────────

def _flag_enabled() -> bool:
    """Return True when the SQL deep extractor is enabled (default: True)."""
    val = os.environ.get("SQL_DEEP_EXTRACTOR_ENABLED", "true").strip().lower()
    return val not in ("false", "0", "no", "off")


# ── public extractor class ─────────────────────────────────────────────────────

class SqlDeepExtractor:
    """Primary extractor for ``.sql`` files.

    When ``SQL_DEEP_EXTRACTOR_ENABLED=false`` this delegates to the legacy
    ``SchemaSqlExtractor`` so existing DDL extraction continues unchanged.

    Usage
    -----
    Instantiate once and call ``extract()`` per file, or call the module-level
    helper ``parse_sql_deep(content, source_file)`` for embedded-SQL callers.
    """

    kind = "sql_deep"

    @staticmethod
    def _get_legacy():  # type: ignore[return]
        """Lazy import to avoid circular import at module load time."""
        from companybrain.extractors.schema_sql import SchemaSqlExtractor  # noqa: PLC0415
        return SchemaSqlExtractor()

    def supports(self, path: Path) -> bool:  # noqa: D102
        return path.suffix.lower() == ".sql"

    def extract(self, path: Path, content: str, *, repo: str = "") -> ExtractedBatch:
        """Extract all SQL statements from *content*.

        When the feature flag is off, returns the legacy DDL-only extraction
        unchanged.  When on, returns an ``ExtractedBatch`` with an attached
        ``_sql_deep_batch`` attribute containing ``SqlDeepBatch``.
        """
        legacy = self._get_legacy()
        if not _flag_enabled():
            return legacy.extract(path, content, repo=repo)

        deep = parse_sql_deep(content, source_file=str(path), repo=repo)

        # Build a minimal ExtractedBatch so the pipeline can continue.  The
        # DDL portion (tables, columns, indexes) is still extracted by the
        # legacy extractor and merged in — this ensures Gate-0 (schema parity)
        # is not regressed.
        legacy_batch = legacy.extract(path, content, repo=repo)

        # Attach the deep batch for consumers that know about it.
        setattr(legacy_batch, "_sql_deep_batch", deep)
        return legacy_batch


# ── core parser ────────────────────────────────────────────────────────────────

def parse_sql_deep(
    content: str,
    *,
    source_file: str = "",
    repo: str = "",
    confidence_tier: str = TIER_LITERAL_STRING,
    dialect: Optional[str] = None,
) -> SqlDeepBatch:
    """Parse *content* as SQL and return a ``SqlDeepBatch``.

    This is the shared entry-point used by both ``SqlDeepExtractor`` (for .sql
    files) and ``SqlEmbeddedScanner`` (for embedded SQL in Java/Python).

    Parameters
    ----------
    content:          Raw SQL text.
    source_file:      Path of the containing file (for attribution).
    repo:             Repository name.
    confidence_tier:  Override the default tier for all statements produced.
                      ``SqlEmbeddedScanner`` passes the appropriate tier based
                      on the Java context it found the SQL in.
    dialect:          sqlglot dialect hint (e.g. "postgres", "mysql", "tsql").
                      When ``None`` sqlglot uses its default heuristics.
    """
    batch = SqlDeepBatch(file=source_file, repo=repo)

    if not _SQLGLOT_AVAILABLE:
        # Graceful degradation — record raw text with unknown type.
        _add_unparseable(batch, content, source_file, repo, confidence_tier, "sqlglot not installed")
        return batch

    # sqlglot.parse returns a list of Statement | None
    try:
        statements = sqlglot.parse(content, dialect=dialect, error_level=sqlglot.ErrorLevel.WARN)
    except Exception as exc:  # noqa: BLE001
        _add_unparseable(batch, content, source_file, repo, confidence_tier, str(exc))
        return batch

    # Pre-compute line offsets for start/end line attribution.
    lines = content.split("\n")
    line_starts: list[int] = []  # character offsets of each line start
    off = 0
    for ln in lines:
        line_starts.append(off)
        off += len(ln) + 1  # +1 for the \n

    for raw_stmt in statements:
        if raw_stmt is None:
            continue

        raw_sql = raw_stmt.sql(dialect=dialect or "")
        if not raw_sql.strip():
            continue

        # Statement type
        stmt_type = _classify_statement(raw_stmt)

        # Tables
        tables = _extract_tables(raw_stmt)

        # Columns
        columns = _extract_columns(raw_stmt)

        # Adjust tier based on whether the SQL has placeholders.
        effective_tier = _adjust_tier(raw_sql, confidence_tier)

        # Line range — find the raw text in content.
        line_start, line_end = _locate_in_content(str(raw_stmt), content, line_starts)

        # Column lineage for SELECT statements.
        lineage: dict[str, set[str]] = {}
        if stmt_type == "SELECT":
            lineage = _extract_lineage(raw_stmt, dialect)

        stmt = EmbeddedSqlStatement(
            raw_sql=raw_sql,
            stmt_type=stmt_type,
            tables=tables,
            columns=columns,
            confidence_tier=effective_tier,
            line_start=line_start,
            line_end=line_end,
            source_file=source_file,
            repo=repo,
            lineage=lineage,
        )
        batch.add(stmt)

    return batch


# ── internal helpers ───────────────────────────────────────────────────────────

def _classify_statement(node: "exp.Expression") -> str:
    """Return an uppercase DML/DDL type for the root node."""
    _MAP = {
        exp.Select: "SELECT",
        exp.Insert: "INSERT",
        exp.Update: "UPDATE",
        exp.Delete: "DELETE",
        exp.Create: "CREATE",
        exp.Drop: "DROP",
        exp.Alter: "ALTER",
        exp.Merge: "MERGE",
        exp.With: "SELECT",  # CTE; unwrap to SELECT
    }
    for klass, name in _MAP.items():
        if isinstance(node, klass):
            return name
    # Fallback: check the first keyword token of the original SQL text.
    first = str(node).strip().split()[0].upper() if str(node).strip() else "OTHER"
    return first if first in ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE", "DROP", "ALTER", "MERGE", "TRUNCATE", "CALL") else "OTHER"


def _extract_tables(node: "exp.Expression") -> list[str]:
    """Return a deduplicated, lowercase list of table names referenced."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for table in node.find_all(exp.Table):
        name = table.name
        if name and name.lower() not in seen_set:
            seen_set.add(name.lower())
            seen.append(name.lower())
    return seen


def _extract_columns(node: "exp.Expression") -> list[str]:
    """Return a deduplicated list of column names referenced (unqualified)."""
    seen: list[str] = []
    seen_set: set[str] = set()
    for col in node.find_all(exp.Column):
        name = col.name
        if name and name.lower() not in seen_set:
            seen_set.add(name.lower())
            seen.append(name.lower())
    return seen


def _extract_lineage(node: "exp.Expression", dialect: Optional[str]) -> dict[str, set[str]]:
    """Best-effort column-level lineage for SELECT statements.

    Returns {output_alias → {source_table.column, …}} or {} on failure.
    """
    try:
        import sqlglot.lineage as lin  # local import to avoid circular issues
        scope = lin.lineage(node.sql(dialect=dialect or ""), dialect=dialect)
        result: dict[str, set[str]] = {}
        for col_node in scope.find_all(exp.Column):
            alias = col_node.alias_or_name
            table = col_node.table or ""
            col = col_node.name
            key = alias if alias else col
            src = f"{table}.{col}" if table else col
            result.setdefault(key, set()).add(src)
        return result
    except Exception:  # noqa: BLE001
        return {}


def _adjust_tier(raw_sql: str, base_tier: str) -> str:
    """Upgrade/downgrade tier based on SQL content."""
    if base_tier == TIER_DYNAMIC_CONCAT:
        return TIER_DYNAMIC_CONCAT
    # If the SQL has JDBC ? or named :param placeholders, mark as prepared.
    import re
    if re.search(r"\?|\:[a-zA-Z_]\w*", raw_sql):
        return TIER_PREPARED_STATEMENT
    return base_tier


def _locate_in_content(stmt_text: str, content: str, line_starts: list[int]) -> tuple[int, int]:
    """Return 1-based (start_line, end_line) for stmt_text in content."""
    # Use a substring search on the original text, not the re-serialized form.
    idx = content.find(stmt_text.strip()[:40].strip())
    if idx < 0:
        return 0, 0
    start = sum(1 for ls in line_starts if ls <= idx)
    end = start + stmt_text.count("\n")
    return start, end


def _add_unparseable(
    batch: SqlDeepBatch,
    content: str,
    source_file: str,
    repo: str,
    tier: str,
    error: str,
) -> None:
    batch.add(EmbeddedSqlStatement(
        raw_sql=content[:4096],
        stmt_type="OTHER",
        tables=[],
        columns=[],
        confidence_tier=tier,
        source_file=source_file,
        repo=repo,
        parse_error=error,
    ))
