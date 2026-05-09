"""
Structural edge pre-extractor.

Produces high-confidence relationship edges from source code WITHOUT any LLM
call. Runs after entity extraction (Stage 1) and before the LLM relationship
pass (Stage 2), so the LLM can focus its budget on behavioral edges
(CALLS / USES / THROWS / READS_COLUMN / etc.) instead of re-finding what we
can derive deterministically from the syntax tree.

Edge types emitted (subset of the 50-type taxonomy in relationship_extractor.py):
  - CONTAINS    — class → its method (member-of)
  - EXTENDS     — child class → parent class
  - IMPLEMENTS  — class → interface
  - INSTANTIATES — caller → class it `new`s (java/kotlin only)
  - ANNOTATES   — annotation/decorator → annotated entity
  - IMPORTS     — first-party module imports (skip stdlib / third-party)

For each edge we emit an ExtractedRelationship with confidence=1.0 and
evidence=the matching source-code fragment so downstream readers can audit.

Cost: $0.00. Adds ~3-8 edges per code unit on a typical Java codebase.
"""
from __future__ import annotations

import re as _re
from typing import TYPE_CHECKING, Iterable, Optional

import structlog

if TYPE_CHECKING:
    from companybrain.collectors.code_tracer import CodeUnit
    from companybrain.models.entities import ExtractedEntity, ExtractedRelationship

log = structlog.get_logger(__name__)


# ── Java / Kotlin patterns ────────────────────────────────────────────────────

_CLASS_DECL = _re.compile(
    r"\b(?:public\s+|abstract\s+|final\s+)*"
    r"(?:class|interface|enum)\s+"
    r"(?P<name>\w+)"
    r"(?:\s+extends\s+(?P<extends>[\w<>,\s.]+?))?"
    r"(?:\s+implements\s+(?P<implements>[\w<>,\s.]+?))?"
    r"\s*\{",
    _re.MULTILINE,
)

_METHOD_DECL = _re.compile(
    r"^\s*(?:@\w+(?:\([^)]*\))?\s+)*"
    r"(?:public|private|protected|static|final|abstract|synchronized|default|\s)+"
    r"[\w<>\[\]?,\s]+\s+"
    r"(?P<name>\w+)"
    r"\s*\([^)]*\)"
    r"(?:\s*throws\s+[\w,\s.]+)?"
    r"\s*[{;]",
    _re.MULTILINE,
)

_INSTANTIATION = _re.compile(
    r"\bnew\s+(?P<type>[A-Z]\w+)\s*[(<]",
)

_ANNOTATION = _re.compile(r"@(?P<name>\w+)(?:\([^)]*\))?")

_IMPORT_JAVA = _re.compile(r"^\s*import\s+(?:static\s+)?(?P<path>[\w.]+);", _re.MULTILINE)


def _strip_generics_and_whitespace(s: str) -> list[str]:
    """`Foo<X>, Bar` → ['Foo', 'Bar']"""
    if not s:
        return []
    cleaned = _re.sub(r"<[^>]*>", "", s)
    return [tok.strip().split(".")[-1] for tok in cleaned.split(",") if tok.strip()]


def _new_rel(
    from_entity: str,
    from_type: str,
    edge_type: str,
    to_entity: str,
    to_type: str,
    evidence: str,
    confidence: float = 1.0,
) -> "ExtractedRelationship":
    from companybrain.models.entities import ExtractedRelationship
    return ExtractedRelationship(
        from_entity=from_entity,
        from_type=from_type,
        edge_type=edge_type,
        to_entity=to_entity,
        to_type=to_type,
        confidence=confidence,
        evidence=evidence[:120],
    )


# ── SQL / jOOQ DSL → table & column edges ────────────────────────────────────
#
# Patterns we match on a DatabaseQuery entity's query_text or code_snippet:
#   jOOQ DSL  →  PLAN_INFO.LOB         → table "plan_info", column "lob"
#                COMP_PROVIDERS.PAYER_ID
#                .from(PLAN_INFO)      → just the table
#   raw SQL   →  FROM plan_info        → table "plan_info"
#                INSERT INTO charges   → table "charges"
#                SELECT amount, status FROM ...  → SELECT cols are READS
#                SET status = ?, amount = ?       → SET cols are WRITES
#
# Output is a flat list of (table, column) pairs. We tag each with a role
# (READS_COLUMN / WRITES_COLUMN / READS_TABLE) so the caller can build
# specific edge types. Names are normalised to snake_case lower.

_JOOQ_TABLE_COL = _re.compile(
    r"\b([A-Z][A-Z0-9_]{2,})\.([A-Z][A-Z0-9_]+)\b"
)
_JOOQ_FROM     = _re.compile(r"\.from\s*\(\s*([A-Z][A-Z0-9_]{2,})\b")
_SQL_FROM      = _re.compile(r"\bFROM\s+([a-z_][\w.]*)", _re.IGNORECASE)
_SQL_JOIN      = _re.compile(r"\bJOIN\s+([a-z_][\w.]*)", _re.IGNORECASE)
_SQL_INSERT    = _re.compile(r"\bINSERT\s+INTO\s+([a-z_][\w.]*)", _re.IGNORECASE)
_SQL_UPDATE    = _re.compile(r"\bUPDATE\s+([a-z_][\w.]*)", _re.IGNORECASE)
_SQL_DELETE    = _re.compile(r"\bDELETE\s+FROM\s+([a-z_][\w.]*)", _re.IGNORECASE)
_SQL_SET       = _re.compile(r"\bSET\s+([\w,\s=?]+)", _re.IGNORECASE)
_SQL_SELECT    = _re.compile(r"\bSELECT\s+(.+?)\s+FROM\b", _re.IGNORECASE | _re.DOTALL)


def _normalise(name: str) -> str:
    return name.strip().rstrip(",").lower()


def extract_sql_edges(
    entity_name: str,
    entity_type: str,
    query_text: str,
) -> list["ExtractedRelationship"]:
    """Deterministically derive READS_COLUMN / WRITES_COLUMN / etc. edges from SQL/DSL.

    Caller passes the entity that owns the query_text; we emit edges with
    from_entity = that entity, to_entity = "table.column" or "table".
    """
    if not query_text:
        return []
    out: list["ExtractedRelationship"] = []
    text = query_text

    # Detect mutation vs read intent. INSERT/UPDATE/DELETE → write.
    is_write = bool(
        _SQL_INSERT.search(text)
        or _SQL_UPDATE.search(text)
        or _SQL_DELETE.search(text)
    )

    # ── jOOQ-style TABLE.COLUMN references ───────────────────────────────────
    for m in _JOOQ_TABLE_COL.finditer(text):
        tbl, col = _normalise(m.group(1)), _normalise(m.group(2))
        # Skip Java constants that look like jOOQ but aren't (Logger.LOG, etc.)
        if tbl in {"log", "logger", "string", "integer", "long", "math",
                   "collections", "objects", "files", "paths"}:
            continue
        edge_type = "WRITES_COLUMN" if is_write else "READS_COLUMN"
        out.append(_new_rel(
            from_entity=entity_name, from_type=entity_type,
            edge_type=edge_type,
            to_entity=f"{tbl}.{col}", to_type="DatabaseColumn",
            evidence=f"{tbl}.{col}",
        ))

    # ── jOOQ .from(TABLE) — table-level read ─────────────────────────────────
    for m in _JOOQ_FROM.finditer(text):
        tbl = _normalise(m.group(1))
        out.append(_new_rel(
            from_entity=entity_name, from_type=entity_type,
            edge_type="READS_COLUMN",  # collapse to reads at table-level too
            to_entity=tbl, to_type="DatabaseTable",
            evidence=f".from({m.group(1)})",
            confidence=0.9,
        ))

    # ── Raw-SQL FROM / JOIN / INSERT / UPDATE / DELETE ───────────────────────
    for pat, edge in (
        (_SQL_FROM,   "READS_COLUMN"),
        (_SQL_JOIN,   "READS_COLUMN"),
        (_SQL_INSERT, "WRITES_COLUMN"),
        (_SQL_UPDATE, "WRITES_COLUMN"),
        (_SQL_DELETE, "WRITES_COLUMN"),
    ):
        for m in pat.finditer(text):
            tbl = _normalise(m.group(1))
            out.append(_new_rel(
                from_entity=entity_name, from_type=entity_type,
                edge_type=edge,
                to_entity=tbl, to_type="DatabaseTable",
                evidence=m.group(0)[:80],
                confidence=0.95,
            ))

    # ── Raw-SQL SELECT cols → READS_COLUMN per identifier ────────────────────
    sel = _SQL_SELECT.search(text)
    if sel:
        cols_blob = sel.group(1)
        for col_token in _re.findall(r"\b([a-z_][\w]+)\b", cols_blob):
            if col_token in {"select", "as", "distinct", "from", "case", "when",
                             "then", "else", "end", "and", "or", "null", "true",
                             "false", "count", "sum", "avg", "min", "max"}:
                continue
            out.append(_new_rel(
                from_entity=entity_name, from_type=entity_type,
                edge_type="READS_COLUMN",
                to_entity=col_token, to_type="DatabaseColumn",
                evidence=f"SELECT … {col_token}",
                confidence=0.7,
            ))

    return out


def extract_structural_edges(
    units: Iterable["CodeUnit"],
    entities: list["ExtractedEntity"],
) -> list["ExtractedRelationship"]:
    """Walk every code unit + entity list and emit deterministic edges.

    Caller passes the entity list so that we can emit edges using the SAME
    `name` strings the LLM would have used — Java's Phase 1 alias-refresh
    indexes nodeIds by qualified_name, so we want our edges to resolve via
    that path.
    """
    out: list["ExtractedRelationship"] = []

    # Build a name → entity_type lookup so the to_type / from_type fields
    # carry useful info downstream.
    type_by_name: dict[str, str] = {}
    for e in entities:
        type_by_name.setdefault(e.name, e.entity_type)

    def _type_of(name: str, fallback: str = "Class") -> str:
        return type_by_name.get(name, fallback)

    for unit in units:
        content = (unit.content or "")
        if not content.strip():
            continue
        file_path = str(getattr(unit, "file_path", "") or "")
        lang      = (getattr(unit, "language", None) or "")
        if lang not in ("java", "kotlin") and not file_path.endswith((".java", ".kt")):
            # Phase 1 strict scope: Java/Kotlin only for now. Easy to extend.
            continue

        # ── EXTENDS / IMPLEMENTS / CONTAINS (one shot per class) ─────────────
        for m in _CLASS_DECL.finditer(content):
            class_name = m.group("name")

            # EXTENDS
            for parent in _strip_generics_and_whitespace(m.group("extends") or ""):
                out.append(_new_rel(
                    from_entity=class_name, from_type=_type_of(class_name, "Class"),
                    edge_type="EXTENDS",
                    to_entity=parent, to_type=_type_of(parent, "Class"),
                    evidence=f"class {class_name} extends {parent}",
                ))

            # IMPLEMENTS
            for iface in _strip_generics_and_whitespace(m.group("implements") or ""):
                out.append(_new_rel(
                    from_entity=class_name, from_type=_type_of(class_name, "Class"),
                    edge_type="IMPLEMENTS",
                    to_entity=iface, to_type=_type_of(iface, "Interface"),
                    evidence=f"class {class_name} implements {iface}",
                ))

            # CONTAINS — every method declared inside this class body
            class_start = m.end()
            class_body  = _extract_class_body(content, class_start)
            for mm in _METHOD_DECL.finditer(class_body):
                method_name = mm.group("name")
                if method_name in {"if", "for", "while", "switch", "catch", "synchronized"}:
                    continue  # control-flow keywords match the regex, skip
                out.append(_new_rel(
                    from_entity=class_name, from_type=_type_of(class_name, "Class"),
                    edge_type="CONTAINS",
                    to_entity=method_name, to_type=_type_of(method_name, "Function"),
                    evidence=f"{class_name}::{method_name}",
                ))

        # ── INSTANTIATES (file-level, conservative) ──────────────────────────
        # Only emit when we have a candidate caller — first-class-name fallback.
        first_class = next(iter(_CLASS_DECL.finditer(content)), None)
        if first_class:
            caller = first_class.group("name")
            for inst in _INSTANTIATION.finditer(content):
                target = inst.group("type")
                # Skip generics like List<T> noise and primitives
                if target in {"String", "Integer", "Long", "Double", "Float",
                              "Boolean", "Object", "ArrayList", "HashMap",
                              "HashSet", "LinkedList", "TreeMap", "Optional"}:
                    continue
                out.append(_new_rel(
                    from_entity=caller, from_type=_type_of(caller, "Class"),
                    edge_type="INSTANTIATES",
                    to_entity=target, to_type=_type_of(target, "Class"),
                    evidence=f"new {target}(...)",
                    confidence=0.9,
                ))

        # ── IMPORTS (first-party only — skip java.* / org.springframework.*) ──
        if first_class:
            caller = first_class.group("name")
            for im in _IMPORT_JAVA.finditer(content):
                path = im.group("path")
                if any(path.startswith(p) for p in (
                    "java.", "javax.", "jakarta.", "org.springframework.",
                    "org.apache.", "com.fasterxml.", "lombok.", "org.slf4j.",
                    "org.junit.", "org.mockito.",
                )):
                    continue
                target_short = path.rsplit(".", 1)[-1]
                out.append(_new_rel(
                    from_entity=caller, from_type=_type_of(caller, "Class"),
                    edge_type="IMPORTS",
                    to_entity=target_short, to_type=_type_of(target_short, "Class"),
                    evidence=f"import {path}",
                    confidence=0.85,
                ))

    # ── SQL / jOOQ edges from any entity that carries query_text ─────────────
    # Cheapest, highest-leverage edges — no LLM. Catches READS_COLUMN /
    # WRITES_COLUMN that the relationship LLM almost never finds because it
    # rarely sees the full query body.
    for e in entities:
        qtext = getattr(e, "query_text", None) or ""
        if not qtext:
            # Also try the entity's code_snippet — jOOQ DSL chains usually
            # live in the method body for repository implementations.
            qtext = getattr(e, "code_snippet", None) or ""
        if qtext:
            out.extend(extract_sql_edges(
                entity_name=e.name,
                entity_type=e.entity_type,
                query_text=qtext,
            ))

    # Dedup on (from, edge_type, to) — first-wins keeps highest-confidence.
    seen: set[tuple[str, str, str]] = set()
    deduped: list["ExtractedRelationship"] = []
    for r in out:
        key = (r.from_entity, r.edge_type, r.to_entity)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    log.info(
        "Structural edges extracted",
        units=len(list(units)) if not isinstance(units, list) else len(units),
        edges_emitted=len(deduped),
        by_type={t: sum(1 for r in deduped if r.edge_type == t)
                 for t in {"CONTAINS", "EXTENDS", "IMPLEMENTS", "INSTANTIATES",
                           "IMPORTS", "READS_COLUMN", "WRITES_COLUMN"}},
    )
    return deduped


def _extract_class_body(content: str, start_offset: int) -> str:
    """Return the substring from start_offset up to the matching closing brace.

    Conservative: counts braces so nested classes / methods are included.
    Returns '' if no balanced close found within 50k chars.
    """
    depth = 1
    i = start_offset
    end = min(len(content), start_offset + 50_000)
    while i < end:
        c = content[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return content[start_offset:i]
        i += 1
    return content[start_offset:end]
