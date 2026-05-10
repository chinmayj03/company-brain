"""
ExtractionLoop — ADR-0041 Phase 2.

Wraps the existing EntityExtractor with a call-graph-following loop:

  Stage 1: extract entities from initial code_units (controller → service)
  → ReferenceResolver finds unresolved call targets (e.g. "CompetitorRepository")
  → AdaptiveLocator maps them to file paths
  → Load those files → extract again
  → Repeat until max_hops reached or no new references found

This makes extraction depth-first along the actual call chain rather than
relying on the CodeTracer's pre-computed FocalContext (which may not follow
deep enough for complex chains).

Design constraints:
  - Max hops: configurable, default 2 (to stay within cost ceiling)
  - Max NEW files per hop: 3 (prevent fan-out explosion)
  - Non-fatal: if a hop fails, the loop logs and continues with what it has
  - Zero new LLM calls if all hops are structurally resolved
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import structlog

from companybrain.models.entities import ExtractedEntity, ExtractedRelationship
from companybrain.collectors.code_tracer import CodeUnit
from companybrain.pipeline.reference_resolver import ReferenceResolver, build_field_index, CallRef
from companybrain.pipeline.adaptive_locator import AdaptiveLocator
from companybrain.pipeline.ast_analyzer import ASTAnalyzer

log = structlog.get_logger(__name__)

_MAX_NEW_FILES_PER_HOP = 3


@dataclass
class ExtractionLoopResult:
    entities:      list[ExtractedEntity]      = field(default_factory=list)
    relationships: list[ExtractedRelationship] = field(default_factory=list)
    extra_units:   list[CodeUnit]             = field(default_factory=list)
    hops_taken:    int                        = 0
    files_followed: list[str]                = field(default_factory=list)


class ExtractionLoop:
    """
    Call-graph-following extraction loop.

    Usage:
        loop = ExtractionLoop(repo_root="/path/to/repo", max_hops=2)
        result = await loop.run(
            initial_entities=entities,
            initial_relationships=relationships,
            initial_units=focal_context.code_units,
            extractor=entity_extractor,
            focal_context=focal_context,
            l2=l2,
        )
        # result.entities has the full call chain
    """

    def __init__(
        self,
        repo_root: "str | Path",
        max_hops: int = 2,
        max_files_per_hop: int = _MAX_NEW_FILES_PER_HOP,
    ) -> None:
        self._root             = Path(repo_root)
        self._max_hops         = max_hops
        self._max_files_per_hop = max_files_per_hop
        self._resolver         = ReferenceResolver()
        self._locator          = AdaptiveLocator(repo_root)
        self._analyzer         = ASTAnalyzer()

    async def run(
        self,
        initial_entities:      list[ExtractedEntity],
        initial_relationships: list[ExtractedRelationship],
        initial_units:         list[CodeUnit],
        *,
        extractor,         # EntityExtractor — circular import avoided via duck typing
        focal_context,     # FocalContext — for language/role hints
        l2,                # L2SharedContext
    ) -> ExtractionLoopResult:
        """
        Run the extraction loop until max_hops is reached or the call chain
        is fully resolved.
        """
        result = ExtractionLoopResult(
            entities=list(initial_entities),
            relationships=list(initial_relationships),
            extra_units=[],
        )

        # Warm the locator with all units we already have
        all_units = list(initial_units)
        self._locator.build_index(all_units)

        # Build the field index from AST data
        symbol_tables = []
        for unit in initial_units:
            try:
                table = self._analyzer.analyze(unit)
                if table:
                    symbol_tables.append(table)
            except Exception:
                pass

        field_index = build_field_index(result.entities, symbol_tables)

        seen_files: set[str] = {u.file_path for u in initial_units}

        for hop in range(1, self._max_hops + 1):
            unresolved = self._resolver.find_unresolved(
                result.entities,
                result.relationships,
                field_index,
                max_distance=hop,
            )
            if not unresolved:
                log.info("[extraction-loop] No unresolved references — stopping", hop=hop)
                break

            new_units = await self._fetch_new_units(
                unresolved, seen_files, initial_units, focal_context
            )

            if not new_units:
                log.info("[extraction-loop] No new files found for unresolved refs", hop=hop)
                break

            # Run extraction on the new units
            for unit in new_units:
                if unit.file_path in seen_files:
                    continue
                seen_files.add(unit.file_path)
                try:
                    new_entities = await extractor._extract_from_code_unit(
                        unit, focal_context, l2, assembly=None
                    )
                    result.entities.extend(new_entities)
                    result.extra_units.append(unit)
                    result.files_followed.append(unit.file_path)
                    # Warm field index with new data
                    table = self._analyzer.analyze(unit)
                    if table:
                        symbol_tables.append(table)
                        for f in table.walk_fields():
                            field_index.setdefault(f["field_name"], f["type_name"])
                    log.info(
                        "[extraction-loop] Followed call to new file",
                        hop=hop,
                        file=unit.file_path,
                        new_entities=len(new_entities),
                    )
                except Exception as e:
                    log.warning("[extraction-loop] Extraction failed for unit (non-fatal)",
                                file=unit.file_path, error=str(e))

            result.hops_taken = hop
            self._locator.build_index(new_units)

        return result

    async def _fetch_new_units(
        self,
        unresolved:    list[CallRef],
        seen_files:    set[str],
        initial_units: list[CodeUnit],
        focal_context,
    ) -> list[CodeUnit]:
        """
        For each unresolved CallRef, locate the file and load its content.
        Returns at most _max_files_per_hop new CodeUnit objects.
        """
        # Infer language from existing units
        language = "java"
        if initial_units:
            language = initial_units[0].language or "java"

        # Infer repo name
        repo_name = ""
        if initial_units:
            repo_name = initial_units[0].repo_name or ""

        new_units: list[CodeUnit] = []

        for ref in unresolved:
            if len(new_units) >= self._max_files_per_hop:
                break
            if not ref.type_hint:
                continue

            class_name = ref.type_hint.split(".")[-1]
            candidates = self._locator.locate(class_name, language=language)

            for candidate_path in candidates:
                rel_path = str(candidate_path.relative_to(self._root))
                if rel_path in seen_files:
                    continue
                if not candidate_path.exists():
                    continue
                # ADR-0045: absolute path, no content — chunker reads from disk
                role = _infer_role(rel_path)
                unit = CodeUnit(
                    file_path=str(candidate_path.resolve()),
                    repo_name=repo_name,
                    role=role,
                    language=language,
                )
                new_units.append(unit)
                log.debug("[extraction-loop] Loaded candidate file", path=rel_path)
                break

        return new_units


# ── Helpers ───────────────────────────────────────────────────────────────────

_ROLE_HINTS = [
    ("controller", "controller"), ("resource",   "controller"),
    ("handler",    "controller"), ("service",     "service"),
    ("serviceimpl","service"),    ("repository",  "repository"),
    ("repo",       "repository"), ("dao",         "repository"),
    ("client",     "client"),     ("adapter",     "client"),
    ("gateway",    "client"),     ("model",       "model"),
    ("entity",     "model"),      ("dto",         "model"),
]


def _infer_role(file_path: str) -> str:
    path_lower = file_path.lower()
    for keyword, role in _ROLE_HINTS:
        if keyword in path_lower:
            return role
    return "service"
