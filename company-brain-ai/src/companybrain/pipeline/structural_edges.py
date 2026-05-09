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
                 for t in {"CONTAINS", "EXTENDS", "IMPLEMENTS", "INSTANTIATES", "IMPORTS"}},
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
