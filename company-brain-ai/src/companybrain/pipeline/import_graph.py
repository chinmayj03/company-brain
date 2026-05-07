"""
ImportGraphAnalyzer — deterministic CALLS edge detection from import statements.

Zero LLM cost. Runs before Stage 2 (LLM relationship extraction) and provides
structural edges that the LLM almost always gets right but wastes tokens reasoning about.

Supported languages:
  Java   — @Autowired field injection, constructor injection, import + usage
  Python — import / from...import + direct usage in class body
  TypeScript/JavaScript — import ... from + usage

Algorithm per file:
  1. Parse import statements → set of imported type names
  2. Find field declarations / constructor params that reference imported types
  3. Find method calls in the class body matching those field names
  4. Emit CALLS edge: current class → imported type, confidence=0.95 (structural)

Edge quality:
  - Confidence 0.95: @Autowired / @Inject (explicit DI wiring)
  - Confidence 0.85: constructor-injected field (very likely real usage)
  - Confidence 0.75: imported and referenced but injection not explicit

Edges are merged with LLM-extracted relationships before deduplication.
The LLM sees the structural edges in context and can correct or supplement them.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog

from companybrain.models.entities import ExtractedEntity, ExtractedRelationship
from companybrain.collectors.code_tracer import CodeUnit

log = structlog.get_logger(__name__)


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class ImportEdge:
    """A CALLS edge detected from import + injection analysis."""
    from_entity:  str    # entity external_id
    from_name:    str    # class name
    from_type:    str    # entity type
    to_class:     str    # injected class name (may not yet be in entity list)
    confidence:   float
    evidence:     str


# ── Main class ────────────────────────────────────────────────────────────────

class ImportGraphAnalyzer:
    """
    Scans code units for import-based CALLS edges without using an LLM.

    Usage:
        edges = ImportGraphAnalyzer().analyze(code_units, entities)
        relationships = edges_to_relationships(edges, entities)
    """

    def analyze(
        self,
        code_units: list[CodeUnit],
        entities: list[ExtractedEntity],
    ) -> list[ImportEdge]:
        """
        Analyze all code units and return detected import edges.
        Only emits edges where BOTH the source and a candidate target exist in entities.
        """
        # Build a fast lookup: class name → entity
        entity_by_class: dict[str, ExtractedEntity] = {}
        for e in entities:
            entity_by_class[e.name] = e
            # Also index by short class name (e.g. "PaymentService" from "com.x.PaymentService")
            short = e.name.split(".")[-1]
            if short != e.name:
                entity_by_class[short] = e

        all_edges: list[ImportEdge] = []
        for unit in code_units:
            if not unit.content:
                continue
            lang = _detect_language(unit.file_path)
            if lang == "java":
                edges = _analyze_java(unit, entity_by_class)
            elif lang in ("ts", "js"):
                edges = _analyze_typescript(unit, entity_by_class)
            elif lang == "python":
                edges = _analyze_python(unit, entity_by_class)
            else:
                continue
            all_edges.extend(edges)

        log.info("[import-graph] Detected %d structural CALLS edges from %d units",
                 len(all_edges), len(code_units))
        return all_edges

    def to_relationships(
        self,
        edges: list[ImportEdge],
        entities: list[ExtractedEntity],
    ) -> list[ExtractedRelationship]:
        """
        Convert ImportEdges to ExtractedRelationship objects.
        Only emits an edge if the target class matches a known entity.
        """
        entity_by_class: dict[str, ExtractedEntity] = {e.name: e for e in entities}
        for e in entities:
            short = e.name.split(".")[-1]
            entity_by_class.setdefault(short, e)

        relationships: list[ExtractedRelationship] = []
        for edge in edges:
            target_entity = entity_by_class.get(edge.to_class)
            if not target_entity:
                continue   # target not in our entity set — skip

            relationships.append(ExtractedRelationship(
                from_entity=edge.from_entity,
                from_type=edge.from_type,
                edge_type="CALLS",
                to_entity=target_entity.external_id,
                to_type=target_entity.entity_type,
                confidence=edge.confidence,
                evidence=edge.evidence,
            ))

        log.info("[import-graph] Resolved %d/%d edges to known entities",
                 len(relationships), len(edges))
        return relationships


# ── Language-specific analyzers ───────────────────────────────────────────────

# Java patterns
_JAVA_AUTOWIRED = re.compile(
    r'@(?:Autowired|Inject|Resource)\s+(?:private|protected|public)?\s+'
    r'(?:final\s+)?(\w+)\s+(\w+)\s*;',
    re.MULTILINE,
)
_JAVA_CONSTRUCTOR_PARAM = re.compile(
    r'public\s+\w+\s*\(([^)]+)\)',   # constructor signature
)
_JAVA_CLASS_DECL = re.compile(
    r'(?:public|private|protected)?\s+(?:class|interface|record)\s+(\w+)',
)

def _analyze_java(unit: CodeUnit, entity_by_class: dict[str, ExtractedEntity]) -> list[ImportEdge]:
    content  = unit.content
    edges: list[ImportEdge] = []

    # Find class name
    class_match = _JAVA_CLASS_DECL.search(content)
    class_name  = class_match.group(1) if class_match else unit.file_path.split("/")[-1].replace(".java", "")

    source_entity = entity_by_class.get(class_name)
    if not source_entity:
        return []   # source class not in entity list

    # @Autowired / @Inject fields — highest confidence (explicit DI)
    for m in _JAVA_AUTOWIRED.finditer(content):
        injected_type = m.group(1)
        field_name    = m.group(2)
        if injected_type in entity_by_class:
            edges.append(ImportEdge(
                from_entity=source_entity.external_id,
                from_name=class_name,
                from_type=source_entity.entity_type,
                to_class=injected_type,
                confidence=0.95,
                evidence=f"@Autowired {injected_type} {field_name}",
            ))

    # Constructor injection — high confidence (Spring standard pattern)
    ctor_match = _JAVA_CONSTRUCTOR_PARAM.search(content)
    if ctor_match:
        params_str = ctor_match.group(1)
        for param in params_str.split(","):
            parts = param.strip().split()
            if len(parts) >= 2:
                param_type = parts[-2]
                param_name = parts[-1]
                if param_type in entity_by_class and not any(
                    e.to_class == param_type for e in edges
                ):
                    edges.append(ImportEdge(
                        from_entity=source_entity.external_id,
                        from_name=class_name,
                        from_type=source_entity.entity_type,
                        to_class=param_type,
                        confidence=0.85,
                        evidence=f"constructor param {param_type} {param_name}",
                    ))

    return edges


# TypeScript / JavaScript patterns
_TS_IMPORT = re.compile(
    r"import\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)
_TS_CLASS_DECL = re.compile(r'class\s+(\w+)')
_TS_INJECTABLE  = re.compile(
    r'(?:private|protected|public|readonly)\s+(?:readonly\s+)?(\w+)\s*:\s*(\w+)',
)

def _analyze_typescript(unit: CodeUnit, entity_by_class: dict[str, ExtractedEntity]) -> list[ImportEdge]:
    content = unit.content
    edges: list[ImportEdge] = []

    class_match = _TS_CLASS_DECL.search(content)
    class_name  = class_match.group(1) if class_match else ""

    source_entity = entity_by_class.get(class_name)
    if not source_entity:
        return []

    # Collect all imported names
    imported: set[str] = set()
    for m in _TS_IMPORT.finditer(content):
        for name in m.group(1).split(","):
            imported.add(name.strip())

    # Find constructor field declarations using imported types
    for m in _TS_INJECTABLE.finditer(content):
        field_name  = m.group(1)
        field_type  = m.group(2)
        if field_type in imported and field_type in entity_by_class:
            edges.append(ImportEdge(
                from_entity=source_entity.external_id,
                from_name=class_name,
                from_type=source_entity.entity_type,
                to_class=field_type,
                confidence=0.85,
                evidence=f"DI field {field_name}: {field_type}",
            ))

    return edges


# Python patterns
_PY_FROM_IMPORT = re.compile(
    r'from\s+[\w.]+\s+import\s+(.+)',
    re.MULTILINE,
)
_PY_CLASS_DECL  = re.compile(r'class\s+(\w+)')
_PY_SELF_FIELD  = re.compile(r'self\.(\w+)\s*=\s*(\w+)\s*\(')

def _analyze_python(unit: CodeUnit, entity_by_class: dict[str, ExtractedEntity]) -> list[ImportEdge]:
    content = unit.content
    edges: list[ImportEdge] = []

    class_match = _PY_CLASS_DECL.search(content)
    class_name  = class_match.group(1) if class_match else ""

    source_entity = entity_by_class.get(class_name)
    if not source_entity:
        return []

    imported: set[str] = set()
    for m in _PY_FROM_IMPORT.finditer(content):
        for name in m.group(1).split(","):
            name = name.strip().split(" as ")[0].strip()
            imported.add(name)

    # self.xxx = SomeClass() patterns — DI via constructor
    for m in _PY_SELF_FIELD.finditer(content):
        field_name   = m.group(1)
        constructor  = m.group(2)
        if constructor in imported and constructor in entity_by_class:
            edges.append(ImportEdge(
                from_entity=source_entity.external_id,
                from_name=class_name,
                from_type=source_entity.entity_type,
                to_class=constructor,
                confidence=0.80,
                evidence=f"self.{field_name} = {constructor}(...)",
            ))

    return edges


# ── Helpers ───────────────────────────────────────────────────────────────────

def _detect_language(file_path: str) -> str:
    if not file_path:
        return ""
    ext = file_path.rsplit(".", 1)[-1].lower()
    return {
        "java": "java",
        "kt":   "kotlin",
        "ts":   "ts",
        "tsx":  "ts",
        "js":   "js",
        "jsx":  "js",
        "py":   "python",
    }.get(ext, "")
