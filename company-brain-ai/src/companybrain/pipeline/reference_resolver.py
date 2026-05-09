"""
ReferenceResolver — ADR-0041 Phase 2.

Given a set of already-extracted entities, finds which SOURCE FILES still need
to be fetched and extracted to follow the call chain further.

Strategy (generalized, no framework hardcoding):
  1. For each entity, look at:
       - code_snippet: scan for `receiver.method(` calls
       - relationships: follow CALLS edges to entities not yet extracted
       - walk_calls from SymbolTable: structural call-site data
  2. Map discovered class/type names to candidate file paths using the
     AdaptiveLocator (next module).
  3. Return a prioritised list of (class_name, candidate_file_paths) tuples
     ordered by call-chain distance from the entry point.

This is what enables "Stage 1 → follow call → Stage 2 → follow call → Stage 3"
without knowing in advance which file contains the repository.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import structlog

from companybrain.models.entities import ExtractedEntity, ExtractedRelationship

log = structlog.get_logger(__name__)

# Regex to find `receiver.method(` in code_snippet / body_text
_CALL_RE = re.compile(r'\b(\w+)\.(\w+)\s*\(')

# Types we consider "resolved" (don't need to follow further)
_PRIMITIVE_TYPES = frozenset({
    "String", "Integer", "Long", "Boolean", "Double", "Float", "Object",
    "List", "Map", "Set", "Optional", "void", "int", "long", "boolean",
    "double", "float", "byte", "char", "short",
})

# Field names that are almost certainly framework-managed infrastructure
_INFRA_RECEIVERS = frozenset({
    "log", "logger", "LOG", "LOGGER", "this", "super", "System",
    "Math", "Objects", "Optional", "String", "Arrays", "Collections",
    "CompletableFuture", "ResponseEntity",
})


@dataclass
class CallRef:
    """A discovered call that hasn't been extracted yet."""
    receiver:      str   # field/variable name (e.g. "competitorsRepo")
    type_hint:     str   # resolved type (e.g. "CompetitorsRepository") — may be ""
    callee_method: str   # called method name
    evidence:      str   # short code snippet showing the call
    distance:      int   # hop count from the entry point


class ReferenceResolver:
    """
    Inspects extracted entities and their code_snippets to find unresolved
    call references (types/classes that were called but not yet extracted).

    Usage:
        resolver = ReferenceResolver()
        unresolved = resolver.find_unresolved(entities, relationships, field_index)
    """

    def find_unresolved(
        self,
        entities: list[ExtractedEntity],
        relationships: list[ExtractedRelationship],
        field_index: dict[str, str],
        *,
        max_distance: int = 3,
    ) -> list[CallRef]:
        """
        Returns unresolved CallRef objects — class names that appear in call
        expressions but don't have a corresponding entity yet.

        field_index: maps field-name → type-name for the current extraction
                     context (built from AST field data or import analysis).
        """
        extracted_names: set[str] = {e.name.split(".")[-1] for e in entities}
        already_queued:  set[str] = set()
        unresolved:      list[CallRef] = []

        # ── Step 1: Scan code_snippets for method calls ───────────────────────
        for entity in entities:
            if not entity.code_snippet:
                continue
            distance = _entity_distance(entity, entities, relationships)
            for match in _CALL_RE.finditer(entity.code_snippet):
                receiver    = match.group(1)
                callee_meth = match.group(2)

                if receiver in _INFRA_RECEIVERS:
                    continue

                # Try to resolve receiver → type using the field index
                type_hint = field_index.get(receiver, "")
                if not type_hint:
                    # Capitalise first letter as a heuristic (fieldName → TypeName)
                    if receiver and receiver[0].islower():
                        # Common pattern: competitorService → CompetitorService
                        type_hint = receiver[0].upper() + receiver[1:]
                    else:
                        type_hint = receiver

                short_type = type_hint.split(".")[-1]
                if short_type in _PRIMITIVE_TYPES or short_type in extracted_names:
                    continue
                if short_type in already_queued:
                    continue
                if distance >= max_distance:
                    continue

                already_queued.add(short_type)
                context_line = _context_line(entity.code_snippet, match.start())
                unresolved.append(CallRef(
                    receiver      = receiver,
                    type_hint     = type_hint,
                    callee_method = callee_meth,
                    evidence      = context_line[:80],
                    distance      = distance + 1,
                ))

        # ── Step 2: Follow CALLS relationships to unextracted entities ────────
        to_entity_names = {r.to_entity.split("::")[-1].split(".")[-1] for r in relationships}
        for name in to_entity_names:
            if name not in extracted_names and name not in already_queued:
                already_queued.add(name)
                unresolved.append(CallRef(
                    receiver      = "",
                    type_hint     = name,
                    callee_method = "",
                    evidence      = f"CALLS edge target: {name}",
                    distance      = 1,
                ))

        # Sort by distance (shortest first) for prioritized extraction
        unresolved.sort(key=lambda c: c.distance)
        if unresolved:
            log.info(
                "[reference-resolver] Found unresolved call references",
                count=len(unresolved),
                top=[f"{c.type_hint}.{c.callee_method}" for c in unresolved[:5]],
            )
        return unresolved


# ── Helpers ───────────────────────────────────────────────────────────────────

def _entity_distance(
    entity: ExtractedEntity,
    all_entities: list[ExtractedEntity],
    relationships: list[ExtractedRelationship],
) -> int:
    """
    Estimate hop distance of entity from the ApiEndpoint entry point.
    0 = ApiEndpoint, 1 = direct callee, 2 = callee of callee, etc.
    """
    if entity.entity_type == "ApiEndpoint":
        return 0
    # Build reverse lookup: to_entity → min distance of from_entity
    # Simple BFS from ApiEndpoints
    from collections import deque
    endpoint_ids = {e.external_id for e in all_entities if e.entity_type == "ApiEndpoint"}
    dist: dict[str, int] = {eid: 0 for eid in endpoint_ids}
    queue: deque[str] = deque(dist.keys())
    id_to_entity = {e.external_id: e for e in all_entities}
    while queue:
        current_id = queue.popleft()
        current_dist = dist[current_id]
        for rel in relationships:
            if rel.from_entity == current_id and rel.to_entity not in dist:
                dist[rel.to_entity] = current_dist + 1
                queue.append(rel.to_entity)
    return dist.get(entity.external_id, 2)  # default 2 if not reachable


def _context_line(text: str, char_offset: int) -> str:
    """Return the line containing char_offset from text."""
    start = text.rfind("\n", 0, char_offset) + 1
    end   = text.find("\n", char_offset)
    if end == -1:
        end = len(text)
    return text[start:end].strip()


def build_field_index(
    entities: list[ExtractedEntity],
    symbol_tables: list,
) -> dict[str, str]:
    """
    Build a mapping of field_name → type_name from SymbolTable field data.
    Used by ReferenceResolver to dereference `receiver.method()` patterns.

    symbol_tables: list of SymbolTable objects (from ASTAnalyzer).
    """
    index: dict[str, str] = {}
    for table in symbol_tables:
        if not hasattr(table, "walk_fields"):
            continue
        for f in table.walk_fields():
            index[f["field_name"]] = f["type_name"]
    return index
