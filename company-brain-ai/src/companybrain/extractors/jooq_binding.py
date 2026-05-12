"""
jOOQ generated-code binding extractor (S2) — ADR-0058.

When jOOQ generates a ``Tables.java`` (and its sibling per-table classes) the
columns become Java constants like ``PLAN_INFO.PAYER_PLAN_ID``. Code that
uses jOOQ DSL references those constants, not the underlying SQL column —
without this extractor the brain can't follow ``READS_COLUMN
PLAN_INFO.PAYER_PLAN_ID`` back to a ``DatabaseColumn`` entity.

This module emits JooqTableBinding and JooqFieldBinding records. The actual
URN resolution (binding → DatabaseColumn) is performed in ``schema_resolver``
once all schemas have been parsed.

Generated jOOQ tables look roughly like:

    public class PlanInfo extends TableImpl<PlanInfoRecord> {
        public static final PlanInfo PLAN_INFO = new PlanInfo();
        public final TableField<PlanInfoRecord, String> PAYER_PLAN_ID =
            createField(DSL.name("payer_plan_id"), SQLDataType.VARCHAR(64).nullable(false), this, "");
        ...
    }

The parser is regex-driven — robust enough for jOOQ's deterministic output.
We deliberately avoid a full Java parser because jOOQ's generation pattern
is stable and the cost of pulling in a Java parser would dwarf the benefit.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable

from companybrain.extractors.base import Extractor
from companybrain.models.entities import (
    EDGE_BINDS_TO_COLUMN,
    EDGE_BINDS_TO_TABLE,
    ExtractedBatch,
    JooqFieldBinding,
    JooqTableBinding,
    SchemaEdge,
    SchemaExtractedBatch,
)


_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)

# Class declaration: "public class PlanInfo extends TableImpl<PlanInfoRecord> {"
_CLASS_RE = re.compile(
    r"public\s+class\s+(\w+)\s+extends\s+TableImpl\s*<",
)

# Static table instance: "public static final PlanInfo PLAN_INFO = new PlanInfo();"
_TABLE_INSTANCE_RE = re.compile(
    r"public\s+static\s+final\s+(\w+)\s+([A-Z][A-Z0-9_]*)\s*=\s*new\s+\1\s*\(",
)

# Field declaration: handle both styles.
#   public final TableField<R, T> COL = createField(DSL.name("col"), SQLDataType.XYZ.nullable(...), this, "");
# We capture the type expression up to ", this" (the 3rd createField argument),
# falling back to the closing paren of the call. Type expressions can contain
# nested parens like "SQLDataType.VARCHAR(64).nullable(false)" so we cannot
# stop at the first `)`.
_FIELD_RE = re.compile(
    r"public\s+(?:final\s+)?TableField<[^>]+>\s+"
    r"(?P<const>[A-Z][A-Z0-9_]*)\s*=\s*createField\s*\(\s*"
    r"(?:DSL\.\s*name\s*\(\s*\"(?P<col>[^\"]+)\"\s*\)|\"(?P<col2>[^\"]+)\")\s*,\s*"
    r"(?P<sqltype>.+?)"
    r"\s*,\s*this\b",
    re.DOTALL,
)

# Table name argument inside the class constructor; we usually pluck it from
# the super(...) call: super(DSL.name("plan_info"), ...);
_SUPER_NAME_RE = re.compile(
    r"super\s*\(\s*(?:DSL\.\s*name\s*\(\s*\"(?P<n1>[^\"]+)\"\s*\)|\"(?P<n2>[^\"]+)\")",
)


class JooqTablesExtractor:
    """Extractor for ``target/generated-sources/jooq/.../Tables.java`` and the
    per-table classes generated alongside it.

    The orchestrator's schema-aware pass invokes ``scan_jooq_bindings`` to
    walk the repo because ``.java`` files are normally claimed by the code
    chunker — this extractor is wired in by file-path heuristics rather than
    the universal-extraction dispatch.
    """

    kind = "schema_jooq"

    def supports(self, path: Path) -> bool:
        s = str(path).replace("\\", "/")
        if not s.endswith(".java"):
            return False
        return "/generated-sources/jooq/" in s or "/generated/jooq/" in s

    def extract(self, path: Path, content: str, *, repo: str = "") -> ExtractedBatch:
        batch = parse_jooq_java(str(path), content, repo=repo)
        out = batch.to_extracted_batch()
        setattr(out, "_schema_batch", batch)
        return out


def parse_jooq_java(source_file: str, content: str, *, repo: str = "") -> SchemaExtractedBatch:
    """Parse one jOOQ-generated .java file. Returns an empty batch if the file
    doesn't look like a generated jOOQ table class.
    """
    batch = SchemaExtractedBatch(file=source_file, repo=repo, extractor_kind="schema_jooq")

    pkg_match = _PACKAGE_RE.search(content)
    package = pkg_match.group(1) if pkg_match else ""

    class_match = _CLASS_RE.search(content)
    if class_match is None:
        return batch
    class_name = class_match.group(1)
    fqcn = f"{package}.{class_name}" if package else class_name

    # Static table-instance line.
    inst_match = _TABLE_INSTANCE_RE.search(content)
    if inst_match is None:
        return batch
    java_constant = inst_match.group(2)

    # DB table name from super(...) call.
    super_match = _SUPER_NAME_RE.search(content)
    db_table_name = ""
    if super_match:
        db_table_name = super_match.group("n1") or super_match.group("n2") or ""
    if not db_table_name:
        # Fall back to lowercase of the constant.
        db_table_name = java_constant.lower()

    table_binding = JooqTableBinding(
        jooq_class=fqcn,
        java_constant=java_constant,
        db_table_urn=f"table::public.{db_table_name}",
        db_table_name=db_table_name,
        source_file=source_file,
        repo=repo,
    )
    batch.jooq_tables.append(table_binding)
    batch.edges.append(SchemaEdge(
        edge_type=EDGE_BINDS_TO_TABLE,
        from_urn=table_binding.external_id,
        to_urn=table_binding.db_table_urn,
        evidence=f"jOOQ class {fqcn} → table {db_table_name}",
    ))

    for fm in _FIELD_RE.finditer(content):
        const = fm.group("const")
        db_col = fm.group("col") or fm.group("col2") or const.lower()
        sqltype = _normalize_sqltype(fm.group("sqltype"))
        jooq_constant = f"{java_constant}.{const}"
        col_urn = f"column::{table_binding.db_table_urn}.{db_col}"
        field_binding = JooqFieldBinding(
            jooq_constant=jooq_constant,
            db_column_urn=col_urn,
            db_column_name=db_col,
            db_type=sqltype,
            source_file=source_file,
            repo=repo,
        )
        batch.jooq_fields.append(field_binding)
        batch.edges.append(SchemaEdge(
            edge_type=EDGE_BINDS_TO_COLUMN,
            from_urn=field_binding.external_id,
            to_urn=col_urn,
            evidence=f"{jooq_constant} → {db_table_name}.{db_col} ({sqltype})",
        ))

    return batch


def _normalize_sqltype(raw: str) -> str:
    """Convert ``SQLDataType.VARCHAR(64).nullable(false)`` → ``VARCHAR(64)``.

    Strips any package qualifier prefix (``org.jooq.impl.SQLDataType.``) and
    any chained call after the type token (``.nullable(...)``,
    ``.defaultValue(...)``, ``.asConvertedDataType(...)``, ...). Preserves the
    type's size argument (the parenthesised expression directly after the
    type identifier).
    """
    s = raw.strip()

    # Walk dotted segments left-to-right until we hit the type token. The type
    # token is the first UPPERCASE_IDENTIFIER segment. Everything before it is
    # package / class qualifier (e.g. "org.jooq.impl.SQLDataType.VARCHAR(...)").
    segments: list[str] = []
    cur = ""
    depth = 0
    for ch in s:
        if ch == "(":
            depth += 1
            cur += ch
            continue
        if ch == ")":
            depth -= 1
            cur += ch
            continue
        if ch == "." and depth == 0:
            segments.append(cur)
            cur = ""
            continue
        cur += ch
    if cur:
        segments.append(cur)

    # Find first segment whose leading identifier is all-uppercase — the type.
    type_idx = -1
    for i, seg in enumerate(segments):
        head = seg.split("(", 1)[0]
        if head and head.isupper():
            type_idx = i
            break
    if type_idx < 0:
        return s
    return segments[type_idx]


def scan_jooq_bindings(repo_root: Path, *, repo_name: str) -> SchemaExtractedBatch:
    """Walk ``repo_root`` for jOOQ-generated table classes and parse them all.

    Recognises both Maven (``target/generated-sources/jooq``) and Gradle
    (``build/generated/sources/jooq``) layouts.
    """
    combined = SchemaExtractedBatch(file="", repo=repo_name, extractor_kind="schema_jooq")
    for path in _iter_jooq_java_files(repo_root):
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        try:
            sub = parse_jooq_java(str(path), content, repo=repo_name)
        except Exception:
            continue
        combined.jooq_tables.extend(sub.jooq_tables)
        combined.jooq_fields.extend(sub.jooq_fields)
        combined.edges.extend(sub.edges)
    return combined


def _iter_jooq_java_files(repo_root: Path) -> Iterable[Path]:
    if not repo_root.exists():
        return []
    out: list[Path] = []
    for marker in ("generated-sources/jooq", "generated/sources/jooq", "generated/jooq"):
        for path in repo_root.rglob(f"*{marker}*/**/*.java"):
            # Exclude the umbrella aggregator (Tables.java aggregates constants
            # by re-exporting the per-table classes; we still want it).
            out.append(path)
    return out
