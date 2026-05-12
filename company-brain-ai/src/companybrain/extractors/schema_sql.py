"""
SQL DDL extractor (S1) — ADR-0058.

Parses CREATE/ALTER TABLE, CREATE INDEX and the common partition/FK forms in
PostgreSQL-flavoured SQL into typed entities (DatabaseTable, DatabaseColumn,
DatabaseIndex, MigrationFile). Drives the MIGRATION_CREATES / MIGRATION_ALTERS
/ INDEXES / FOREIGN_KEY edges.

Design note: the ADR specifies tree-sitter-sql; we use ``sqlparse`` for
statement segmentation and a deterministic column tokenizer for the body.
Rationale: tree-sitter-sql has no maintained PyPI wheel for some platforms,
while sqlparse ships pure-Python wheels on every supported Python. The
extracted shape is identical and the acceptance tests pin behaviour, not
parser identity.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import sqlparse
from sqlparse.sql import Statement

from companybrain.extractors.base import Extractor
from companybrain.models.entities import (
    DatabaseColumn,
    DatabaseIndex,
    DatabaseTable,
    EDGE_FOREIGN_KEY,
    EDGE_INDEXES,
    EDGE_MIGRATION_ALTERS,
    EDGE_MIGRATION_CREATES,
    ExtractedBatch,
    MigrationFile,
    SchemaEdge,
    SchemaExtractedBatch,
)

# Column-level constraint keywords we recognise inside a CREATE TABLE body.
_COLUMN_CONSTRAINTS = frozenset({
    "NOT", "NULL", "PRIMARY", "KEY", "UNIQUE", "DEFAULT", "REFERENCES", "CHECK",
    "GENERATED", "ALWAYS", "STORED", "VIRTUAL", "COLLATE",
})

# Table-level constraint introducers seen at the start of a column-list item.
_TABLE_CONSTRAINT_HEADS = frozenset({
    "CONSTRAINT", "PRIMARY", "UNIQUE", "FOREIGN", "CHECK", "EXCLUDE",
})


class SchemaSqlExtractor:
    """Universal-extraction Extractor for `.sql` files."""

    kind = "schema_sql"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".sql"

    def extract(self, path: Path, content: str, *, repo: str = "") -> ExtractedBatch:
        batch = SchemaExtractedBatch(file=str(path), repo=repo, extractor_kind=self.kind)

        migration = MigrationFile(
            file=str(path),
            repo=repo,
            version=_flyway_version(path.name),
        )

        # Compute statement line ranges from the original text. sqlparse strips
        # whitespace but preserves statement boundaries; map each parsed statement
        # back to its position in the source using a running offset.
        offset = 0
        statements = sqlparse.parse(content)

        # Build a list of (start_line, end_line) per statement using the raw text.
        line_ranges: list[tuple[int, int]] = []
        cursor = 0
        for stmt in statements:
            raw = str(stmt)
            idx = content.find(raw, cursor)
            if idx < 0:
                line_ranges.append((0, 0))
                continue
            start_line = content.count("\n", 0, idx) + 1
            end_line = start_line + raw.count("\n")
            line_ranges.append((start_line, end_line))
            cursor = idx + len(raw)

        for stmt, line_range in zip(statements, line_ranges):
            kind_kw = _statement_kind(stmt)
            if kind_kw is None:
                continue
            # Drop SQL comments before raw-text scanning. sqlparse includes
            # leading line/block comments as part of the next statement, which
            # otherwise tricks our identifier scanner (e.g. a comment that
            # mentions ``ALTER TABLE ADD COLUMN`` makes ``ADD`` look like the
            # table name).
            clean = _strip_sql_comments(str(stmt))
            if kind_kw == "CREATE_TABLE":
                _handle_create_table(clean, batch, migration, line_range, str(path), repo)
            elif kind_kw == "ALTER_TABLE":
                _handle_alter_table(clean, batch, migration, str(path), repo)
            elif kind_kw == "CREATE_INDEX":
                _handle_create_index(clean, batch, str(path), repo)

        if migration.creates or migration.alters:
            batch.migrations.append(migration)

        out = batch.to_extracted_batch()
        # Attach the typed batch so downstream resolver can find it.
        setattr(out, "_schema_batch", batch)
        return out


# ── statement classifier ──────────────────────────────────────────────────────

def _statement_kind(stmt: Statement) -> Optional[str]:
    tokens = [t for t in stmt.flatten() if not t.is_whitespace and not _is_comment(t)]
    if not tokens:
        return None
    head = [t.normalized.upper() for t in tokens[:4]]
    if head[:2] == ["CREATE", "TABLE"]:
        return "CREATE_TABLE"
    if head[:3] == ["CREATE", "UNIQUE", "INDEX"] or head[:2] == ["CREATE", "INDEX"]:
        return "CREATE_INDEX"
    if head[:2] == ["ALTER", "TABLE"]:
        return "ALTER_TABLE"
    return None


def _is_comment(tok) -> bool:
    return tok.ttype is not None and "Comment" in str(tok.ttype)


def _strip_sql_comments(raw: str) -> str:
    """Remove ``--`` line comments and ``/* ... */`` block comments from raw SQL.

    Preserves quoted strings — comment markers inside quotes are kept as-is.
    """
    out: list[str] = []
    i = 0
    n = len(raw)
    in_quote: Optional[str] = None
    while i < n:
        ch = raw[i]
        if in_quote:
            out.append(ch)
            if ch == in_quote:
                in_quote = None
            i += 1
            continue
        if ch in ("'", '"'):
            in_quote = ch
            out.append(ch)
            i += 1
            continue
        if ch == "-" and raw[i:i + 2] == "--":
            nl = raw.find("\n", i)
            if nl == -1:
                break
            i = nl
            continue
        if ch == "/" and raw[i:i + 2] == "/*":
            end = raw.find("*/", i + 2)
            if end == -1:
                break
            i = end + 2
            continue
        out.append(ch)
        i += 1
    return "".join(out)


# ── CREATE TABLE ──────────────────────────────────────────────────────────────

def _handle_create_table(
    raw: str,
    batch: SchemaExtractedBatch,
    migration: MigrationFile,
    line_range: tuple[int, int],
    source_file: str,
    repo: str,
) -> None:
    schema_name, table_name = _parse_qualified_name_after(raw, "TABLE")
    if not table_name:
        return

    # Body inside outermost parentheses.
    body = _outer_parens_body(raw)
    columns: list[DatabaseColumn] = []
    primary_key_cols: list[str] = []
    fk_edges: list[SchemaEdge] = []

    table_urn = f"table::{schema_name}.{table_name}"

    for item in _split_top_level_commas(body):
        head = _first_token(item).upper()
        if head in _TABLE_CONSTRAINT_HEADS:
            pks = _extract_pk_from_table_constraint(item)
            primary_key_cols.extend(pks)
            fk_edges.extend(_extract_fks_from_table_constraint(item, table_urn))
            continue
        col = _parse_column_def(item, table_urn, source_file, repo)
        if col is None:
            continue
        columns.append(col)
        if col.is_primary_key:
            primary_key_cols.append(col.name)

    # Mark PK columns identified at the table level (e.g. `PRIMARY KEY (id, x)`).
    pk_set = {c.lower() for c in primary_key_cols}
    for col in columns:
        if col.name.lower() in pk_set:
            col.is_primary_key = True

    # Detect partitioning clause: "... ) PARTITION BY <STRATEGY> (...)".
    is_partitioned, strategy = _detect_partitioning(raw)

    table = DatabaseTable(
        name=table_name,
        schema=schema_name,
        source_file=source_file,
        line_range=line_range,
        primary_key_columns=list(dict.fromkeys(primary_key_cols)),
        is_partitioned=is_partitioned,
        partition_strategy=strategy,
        repo=repo,
    )
    batch.tables.append(table)
    batch.columns.extend(columns)
    batch.edges.extend(fk_edges)
    batch.edges.append(SchemaEdge(
        edge_type=EDGE_MIGRATION_CREATES,
        from_urn=f"migration::{source_file}",
        to_urn=table.external_id,
    ))
    migration.creates.append(table.external_id)

    # Inline column-level REFERENCES → FK edges.
    for col in columns:
        if col.fk_references:
            batch.edges.append(SchemaEdge(
                edge_type=EDGE_FOREIGN_KEY,
                from_urn=col.external_id,
                to_urn=f"column::table::{col.fk_references}",
                evidence=col.fk_references,
            ))


def _parse_column_def(
    item: str, table_urn: str, source_file: str, repo: str,
) -> Optional[DatabaseColumn]:
    """Parse one comma-separated column definition out of a CREATE TABLE body."""
    text = item.strip().rstrip(",").strip()
    if not text:
        return None

    tokens = _tokenize_column_def(text)
    if len(tokens) < 2:
        return None

    name = tokens[0].strip('"')
    type_str, idx = _consume_type(tokens, 1)
    if not type_str:
        return None

    nullable = True
    default_value: Optional[str] = None
    is_pk = False
    fk_ref: Optional[str] = None

    i = idx
    while i < len(tokens):
        upper = tokens[i].upper()
        if upper == "NOT" and _peek(tokens, i + 1).upper() == "NULL":
            nullable = False
            i += 2
            continue
        if upper == "NULL":
            nullable = True
            i += 1
            continue
        if upper == "PRIMARY" and _peek(tokens, i + 1).upper() == "KEY":
            is_pk = True
            nullable = False
            i += 2
            continue
        if upper == "UNIQUE":
            i += 1
            continue
        if upper == "DEFAULT":
            j = i + 1
            depth = 0
            parts: list[str] = []
            while j < len(tokens):
                tok = tokens[j]
                up = tok.upper()
                if depth == 0 and up in _COLUMN_CONSTRAINTS and up not in ("NULL", "NOT"):
                    # next constraint reached
                    break
                if tok == "(":
                    depth += 1
                elif tok == ")":
                    depth -= 1
                parts.append(tok)
                j += 1
            default_value = " ".join(parts).strip()
            i = j
            continue
        if upper == "REFERENCES":
            # column-level FK: REFERENCES schema.table(col)
            ref_target, j = _parse_references_target(tokens, i + 1)
            if ref_target:
                fk_ref = ref_target
            i = j
            continue
        if upper == "CHECK":
            # skip the parenthesised expression
            j = i + 1
            if j < len(tokens) and tokens[j] == "(":
                depth = 1
                j += 1
                while j < len(tokens) and depth > 0:
                    if tokens[j] == "(":
                        depth += 1
                    elif tokens[j] == ")":
                        depth -= 1
                    j += 1
            i = j
            continue
        if upper == "GENERATED":
            # GENERATED ALWAYS AS (...) [STORED]
            j = i + 1
            while j < len(tokens):
                up2 = tokens[j].upper()
                if tokens[j] == "(":
                    depth = 1
                    j += 1
                    while j < len(tokens) and depth > 0:
                        if tokens[j] == "(":
                            depth += 1
                        elif tokens[j] == ")":
                            depth -= 1
                        j += 1
                    continue
                if up2 in ("STORED", "VIRTUAL"):
                    j += 1
                    break
                if up2 in _COLUMN_CONSTRAINTS - {"GENERATED", "ALWAYS"}:
                    break
                j += 1
            i = j
            continue
        i += 1

    return DatabaseColumn(
        name=name,
        table_urn=table_urn,
        type=type_str,
        nullable=nullable,
        default_value=default_value,
        is_primary_key=is_pk,
        is_foreign_key=fk_ref is not None,
        fk_references=fk_ref,
        source_file=source_file,
        repo=repo,
    )


# ── ALTER TABLE ───────────────────────────────────────────────────────────────

def _handle_alter_table(
    raw: str,
    batch: SchemaExtractedBatch,
    migration: MigrationFile,
    source_file: str,
    repo: str,
) -> None:
    schema_name, table_name = _parse_qualified_name_after(raw, "TABLE")
    if not table_name:
        return
    table_urn = f"table::{schema_name}.{table_name}"

    upper = raw.upper()
    # ADD COLUMN
    if "ADD COLUMN" in upper or " ADD " in upper:
        adds = _parse_alter_adds(raw)
        for col_text in adds:
            col = _parse_column_def(col_text, table_urn, source_file, repo)
            if col is not None:
                batch.columns.append(col)

    batch.edges.append(SchemaEdge(
        edge_type=EDGE_MIGRATION_ALTERS,
        from_urn=f"migration::{source_file}",
        to_urn=table_urn,
    ))
    migration.alters.append(table_urn)


def _parse_alter_adds(raw: str) -> list[str]:
    """Pick out ADD COLUMN payloads from an ALTER TABLE statement."""
    out: list[str] = []
    upper = raw.upper()
    pos = 0
    while True:
        i = upper.find("ADD", pos)
        if i < 0:
            break
        # require word-boundary
        if i > 0 and upper[i - 1].isalnum():
            pos = i + 3
            continue
        j = i + 3
        # optional "COLUMN"
        while j < len(upper) and upper[j].isspace():
            j += 1
        if upper[j:j + 6] == "COLUMN":
            j += 6
        while j < len(upper) and upper[j].isspace():
            j += 1
        # capture until end of statement or next top-level comma followed by ADD/DROP/ALTER
        chunk_end = _scan_until_top_comma_or_semicolon(raw, j)
        chunk = raw[j:chunk_end].strip().rstrip(";").strip()
        if chunk:
            out.append(chunk)
        pos = chunk_end
    return out


def _scan_until_top_comma_or_semicolon(raw: str, start: int) -> int:
    depth = 0
    i = start
    while i < len(raw):
        ch = raw[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif depth == 0 and (ch == "," or ch == ";"):
            return i
        i += 1
    return len(raw)


# ── CREATE INDEX ──────────────────────────────────────────────────────────────

def _handle_create_index(
    raw: str,
    batch: SchemaExtractedBatch,
    source_file: str,
    repo: str,
) -> None:
    upper = raw.upper()
    is_unique = " UNIQUE " in f" {upper} "
    # Find "INDEX <name> ON <table> (<cols>) [WHERE ...]"
    idx = upper.find("INDEX")
    if idx < 0:
        return
    rest = raw[idx + len("INDEX"):].lstrip()
    # Optional IF NOT EXISTS
    if rest.upper().startswith("IF NOT EXISTS"):
        rest = rest[len("IF NOT EXISTS"):].lstrip()
    # Index name (until whitespace or 'ON')
    name, rest = _take_identifier(rest)
    on_idx = rest.upper().find(" ON ")
    if on_idx < 0:
        return
    after_on = rest[on_idx + 4:].lstrip()
    schema_name, table_name, after_table = _split_qualified_then_remainder(after_on)
    # Capture column list inside the next parentheses
    paren_open = after_table.find("(")
    if paren_open < 0:
        return
    cols_body, paren_end = _grab_parens(after_table, paren_open)
    columns = [c.strip().strip('"').split()[0] for c in _split_top_level_commas(cols_body) if c.strip()]
    where = None
    tail = after_table[paren_end + 1:]
    upper_tail = tail.upper()
    where_pos = upper_tail.find("WHERE")
    if where_pos >= 0:
        where = tail[where_pos + 5:].strip().rstrip(";").strip()

    table_urn = f"table::{schema_name}.{table_name}"
    idx_obj = DatabaseIndex(
        name=name,
        table_urn=table_urn,
        columns=columns,
        is_unique=is_unique,
        where_clause=where or None,
        source_file=source_file,
        repo=repo,
    )
    batch.indexes.append(idx_obj)
    batch.edges.append(SchemaEdge(
        edge_type=EDGE_INDEXES,
        from_urn=idx_obj.external_id,
        to_urn=table_urn,
    ))


# ── parser helpers ────────────────────────────────────────────────────────────

def _parse_qualified_name_after(raw: str, after_keyword: str) -> tuple[str, str]:
    """Find ``<after_keyword> [IF NOT EXISTS] [schema.]name`` and return (schema, name)."""
    upper = raw.upper()
    i = upper.find(after_keyword)
    if i < 0:
        return "public", ""
    j = i + len(after_keyword)
    rest = raw[j:].lstrip()
    if rest.upper().startswith("IF NOT EXISTS"):
        rest = rest[len("IF NOT EXISTS"):].lstrip()
    if rest.upper().startswith("ONLY"):
        rest = rest[4:].lstrip()
    name, _ = _take_identifier(rest)
    if "." in name:
        schema, _, n = name.partition(".")
        return schema.strip('"'), n.strip('"')
    return "public", name.strip('"')


def _split_qualified_then_remainder(s: str) -> tuple[str, str, str]:
    name, rest = _take_identifier(s)
    if "." in name:
        schema, _, n = name.partition(".")
        return schema.strip('"'), n.strip('"'), rest
    return "public", name.strip('"'), rest


def _take_identifier(s: str) -> tuple[str, str]:
    """Return (identifier, remainder). Handles ``"quoted name"`` and ``schema.name``."""
    s = s.lstrip()
    if not s:
        return "", ""
    if s.startswith('"'):
        end = s.find('"', 1)
        if end < 0:
            return s, ""
        name = s[: end + 1]
        rest = s[end + 1:]
        # allow `"a"."b"` qualified pair
        if rest.lstrip().startswith("."):
            r2 = rest.lstrip()[1:]
            second, after = _take_identifier(r2)
            left = name.strip('"')
            right = second.strip('"')
            return f"{left}.{right}", after
        return name, rest
    end = 0
    while end < len(s) and (s[end].isalnum() or s[end] in "_$.."):
        end += 1
    return s[:end], s[end:]


def _outer_parens_body(raw: str) -> str:
    """Return the contents of the first top-level parenthesised group."""
    i = raw.find("(")
    if i < 0:
        return ""
    body, _ = _grab_parens(raw, i)
    return body


def _grab_parens(s: str, open_idx: int) -> tuple[str, int]:
    """Return (inner_body, index_of_close_paren)."""
    depth = 0
    for j in range(open_idx, len(s)):
        ch = s[j]
        if ch == "(":
            depth += 1
            if depth == 1:
                start = j + 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return s[start:j], j
    return s[open_idx + 1:], len(s) - 1


def _split_top_level_commas(body: str) -> list[str]:
    """Split a comma-separated body, ignoring commas inside parens."""
    out: list[str] = []
    depth = 0
    last = 0
    in_quote: Optional[str] = None
    for i, ch in enumerate(body):
        if in_quote:
            if ch == in_quote:
                in_quote = None
            continue
        if ch in ("'", '"'):
            in_quote = ch
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        elif ch == "," and depth == 0:
            out.append(body[last:i].strip())
            last = i + 1
    tail = body[last:].strip()
    if tail:
        out.append(tail)
    return out


def _first_token(s: str) -> str:
    s = s.lstrip()
    end = 0
    while end < len(s) and not s[end].isspace() and s[end] not in "(),":
        end += 1
    return s[:end]


def _tokenize_column_def(text: str) -> list[str]:
    """Tokenise a column definition into identifiers, type words, punctuation, and
    parens. Quoted strings stay intact."""
    tokens: list[str] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "(),":
            tokens.append(ch)
            i += 1
            continue
        if ch in ("'", '"', "`"):
            j = i + 1
            while j < n and text[j] != ch:
                # respect doubled-quote escape: '' or ""
                if text[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                j += 1
            tokens.append(text[i:j + 1])
            i = j + 1
            continue
        if ch == "[":
            # treat as part of preceding token (text[]).
            j = text.find("]", i)
            if j < 0:
                tokens.append(text[i:])
                break
            if tokens:
                tokens[-1] = tokens[-1] + text[i:j + 1]
            else:
                tokens.append(text[i:j + 1])
            i = j + 1
            continue
        # generic word run
        j = i
        while j < n and not text[j].isspace() and text[j] not in "(),[]":
            j += 1
        tokens.append(text[i:j])
        i = j
    return tokens


def _consume_type(tokens: list[str], start: int) -> tuple[str, int]:
    """Read the type from ``tokens[start:]`` and return (type_str, next_idx).

    Recognises ``text``, ``text[]``, ``varchar(64)``, ``numeric(10, 2)``,
    ``timestamp with time zone`` and ``character varying(255)``.
    """
    if start >= len(tokens):
        return "", start
    parts: list[str] = [tokens[start]]
    i = start + 1
    # First handle parenthesised modifier: TYPE(...)
    if i < len(tokens) and tokens[i] == "(":
        # capture until matching close paren
        depth = 1
        parts.append("(")
        i += 1
        while i < len(tokens) and depth > 0:
            if tokens[i] == "(":
                depth += 1
            elif tokens[i] == ")":
                depth -= 1
            parts.append(tokens[i])
            i += 1
    # Multi-word built-ins: "WITH TIME ZONE", "WITHOUT TIME ZONE", "VARYING".
    while i < len(tokens):
        up = tokens[i].upper()
        if up in ("VARYING", "PRECISION", "WITH", "WITHOUT", "TIME", "ZONE", "LOCAL"):
            parts.append(tokens[i])
            i += 1
            # Some of these are followed by a parenthesised length spec.
            if i < len(tokens) and tokens[i] == "(":
                depth = 1
                parts.append("(")
                i += 1
                while i < len(tokens) and depth > 0:
                    if tokens[i] == "(":
                        depth += 1
                    elif tokens[i] == ")":
                        depth -= 1
                    parts.append(tokens[i])
                    i += 1
            continue
        break
    # Check for trailing array suffix like ``[]`` attached to the last token.
    type_str = _format_type(parts)
    return type_str, i


def _format_type(parts: list[str]) -> str:
    out: list[str] = []
    for idx, p in enumerate(parts):
        if p == "(":
            if out:
                out[-1] = out[-1] + "("
            else:
                out.append("(")
        elif p == ")":
            if out:
                out[-1] = out[-1] + ")"
            else:
                out.append(")")
        elif p == ",":
            if out:
                out[-1] = out[-1] + ","
            else:
                out.append(",")
        else:
            out.append(p)
    return " ".join(out).replace(" ,", ",").replace("( ", "(").replace(" )", ")")


def _peek(tokens: list[str], idx: int) -> str:
    return tokens[idx] if 0 <= idx < len(tokens) else ""


def _parse_references_target(tokens: list[str], start: int) -> tuple[Optional[str], int]:
    """Parse REFERENCES [schema.]table[(col)] starting at tokens[start]."""
    i = start
    if i >= len(tokens):
        return None, i
    name = tokens[i].strip('"')
    i += 1
    col = ""
    if i < len(tokens) and tokens[i] == "(":
        i += 1
        if i < len(tokens) and tokens[i] != ")":
            col = tokens[i].strip('"')
            i += 1
        while i < len(tokens) and tokens[i] != ")":
            i += 1
        if i < len(tokens) and tokens[i] == ")":
            i += 1
    if "." not in name:
        name = f"public.{name}"
    return (f"{name}.{col}" if col else name), i


def _extract_pk_from_table_constraint(item: str) -> list[str]:
    upper = item.upper()
    pos = upper.find("PRIMARY KEY")
    if pos < 0:
        return []
    paren = item.find("(", pos)
    if paren < 0:
        return []
    body, _ = _grab_parens(item, paren)
    return [c.strip().strip('"') for c in _split_top_level_commas(body)]


def _extract_fks_from_table_constraint(item: str, table_urn: str) -> list[SchemaEdge]:
    upper = item.upper()
    pos = upper.find("FOREIGN KEY")
    if pos < 0:
        return []
    paren = item.find("(", pos)
    if paren < 0:
        return []
    cols_body, end = _grab_parens(item, paren)
    cols = [c.strip().strip('"') for c in _split_top_level_commas(cols_body)]
    refs_pos = upper.find("REFERENCES", end)
    if refs_pos < 0:
        return []
    after = item[refs_pos + len("REFERENCES"):].lstrip()
    target_name, rest = _take_identifier(after)
    target_cols: list[str] = []
    if rest.lstrip().startswith("("):
        body2, _ = _grab_parens(rest, rest.find("("))
        target_cols = [c.strip().strip('"') for c in _split_top_level_commas(body2)]
    if "." not in target_name:
        target_name = f"public.{target_name}"
    edges: list[SchemaEdge] = []
    for i, c in enumerate(cols):
        target_col = target_cols[i] if i < len(target_cols) else (target_cols[0] if target_cols else "")
        edges.append(SchemaEdge(
            edge_type=EDGE_FOREIGN_KEY,
            from_urn=f"column::{table_urn}.{c}",
            to_urn=f"column::table::{target_name}.{target_col}" if target_col else f"table::{target_name}",
            evidence=f"FK({c}) REFERENCES {target_name}({target_col})" if target_col else f"FK({c}) REFERENCES {target_name}",
        ))
    return edges


def _detect_partitioning(raw: str) -> tuple[bool, Optional[str]]:
    upper = raw.upper()
    i = upper.find("PARTITION BY")
    if i < 0:
        return False, None
    tail = raw[i + len("PARTITION BY"):].lstrip()
    word, _ = _take_identifier(tail)
    strategy = word.upper().strip("()") if word else None
    return True, strategy


def _flyway_version(filename: str) -> str:
    """Pluck the Flyway-style version prefix out of a filename: V1__baseline.sql → 'V1'."""
    if not filename:
        return ""
    base = filename
    # Flyway versioned: V<version>__<desc>.sql or R__<desc>.sql or U<version>__<desc>.sql
    for prefix in ("V", "R", "U"):
        if base.startswith(prefix):
            i = 1
            while i < len(base) and (base[i].isalnum() or base[i] in "._"):
                if base[i:i + 2] == "__":
                    return base[:i]
                i += 1
    return ""
