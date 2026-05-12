"""
ExtractionLoop — ADR-0041 Phase 2 / ADR-0042 E1 enhancement.

Wraps the existing EntityExtractor with a call-graph-following loop:

  Stage 1: extract entities from initial code_units (controller → service)
  → ReferenceResolver finds unresolved call targets (e.g. "CompetitorRepository")
  → _resolve_symbol_to_file: ripgrep Tier 1 + LLM Tier 2 (capped at 5 resolves)
  → Load those files → extract again
  → Repeat until max_hops reached or no new references found

ADR-0042 E1 changes:
  - Default max_hops bumped from 2 → 3 (deeper call-graph coverage)
  - Two-tier symbol resolution: ripgrep first, LLM resolver if ambiguous
  - LLM resolver capped at 5 calls per pipeline run (class-level counter)

Design constraints:
  - Max hops: configurable, default 3
  - Max NEW files per hop: 3 (prevent fan-out explosion)
  - Non-fatal: if a hop fails, the loop logs and continues with what it has
  - Zero new LLM calls if all hops are structurally resolved
"""

from __future__ import annotations

import asyncio
import subprocess
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
# Cap LLM resolver calls per pipeline run to control cost (~$0.0005 each)
_MAX_LLM_RESOLVES_PER_RUN = 5


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
        max_hops: int = 3,  # bumped from 2 → 3 per ADR-0042 E1
        max_files_per_hop: int = _MAX_NEW_FILES_PER_HOP,
    ) -> None:
        self._root             = Path(repo_root)
        self._max_hops         = max_hops
        self._max_files_per_hop = max_files_per_hop
        self._resolver         = ReferenceResolver()
        self._locator          = AdaptiveLocator(repo_root)
        self._analyzer         = ASTAnalyzer()
        self._llm_resolve_count = 0  # per-run LLM resolver call counter

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

        ADR-0042 E1 two-tier resolution:
          Tier 1 — ripgrep: regex for class/def/function declarations across ALL languages.
          Tier 2 — LLM: if multiple matches, ask the LLM to pick the right one
                   given the calling context. Capped at _MAX_LLM_RESOLVES_PER_RUN.

        Returns at most _max_files_per_hop new CodeUnit objects.
        """
        language = "java"
        if initial_units:
            language = initial_units[0].language or "java"

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

            # Tier 1: ripgrep declaration search (language-agnostic)
            rg_candidates = await _resolve_symbol_to_file(
                class_name, self._root
            )

            # Tier 2: LLM disambiguation when multiple matches
            if len(rg_candidates) > 1 and self._llm_resolve_count < _MAX_LLM_RESOLVES_PER_RUN:
                caller_hint = getattr(ref, "caller_name", "") or ""
                picked = await _llm_pick_file(class_name, rg_candidates, caller_hint)
                if picked:
                    rg_candidates = [picked]
                self._llm_resolve_count += 1

            # Fall back to AdaptiveLocator if ripgrep found nothing
            if not rg_candidates:
                rg_candidates = list(self._locator.locate(class_name, language=language))

            for candidate_path in rg_candidates:
                rel_path = str(candidate_path.relative_to(self._root))
                if rel_path in seen_files:
                    continue
                try:
                    content = candidate_path.read_text(encoding="utf-8", errors="ignore")
                    role = _infer_role(rel_path)
                    unit = CodeUnit(
                        file_path=rel_path,
                        repo_name=repo_name,
                        role=role,
                        language=language,
                        content=content[:8_000],
                    )
                    new_units.append(unit)
                    log.debug("[extraction-loop] Loaded candidate file", path=rel_path,
                              tier="ripgrep" if rg_candidates else "adaptive_locator")
                    break
                except (OSError, IOError):
                    continue

        return new_units


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _resolve_symbol_to_file(
    symbol_name: str, repo_root: Path
) -> list[Path]:
    """
    ADR-0042 E1, Tier 1: use ripgrep to locate source files that define symbol_name.

    Searches for class/def/function/interface declarations across ALL languages
    with a single regex so language detection is not needed in the orchestrator path.

    Returns a list of Path objects (may be empty or have multiple candidates).
    """
    if not symbol_name or not repo_root.exists():
        return []

    # Language-agnostic pattern: matches Java class/interface, Python def/class,
    # TypeScript class/function/interface, Go func, Ruby class, C# class
    pattern = (
        r"(class|interface|struct|def|function|func)\s+" + symbol_name + r"\b"
    )

    try:
        result = subprocess.run(
            ["rg", "--files-with-matches", "--no-ignore", "-l",
             "-e", pattern, str(repo_root)],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode not in (0, 1):  # 1 = no matches (not an error)
            return []
        paths = []
        for line in result.stdout.strip().splitlines():
            p = Path(line.strip())
            if p.exists():
                paths.append(p)
        return paths
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # ripgrep not available or timed out — fall through to AdaptiveLocator
        return []


async def _llm_pick_file(
    symbol_name: str,
    candidates: list[Path],
    caller_hint: str,
) -> Path | None:
    """
    ADR-0042 E1, Tier 2: ask the LLM to pick the right file when ripgrep
    returns multiple candidates.

    Called at most _MAX_LLM_RESOLVES_PER_RUN times per pipeline run.
    """
    try:
        from companybrain.llm import get_provider, TaskRole, ChatMessage

        candidate_list = "\n".join(
            f"{i+1}. {p}" for i, p in enumerate(candidates[:10])
        )
        prompt = (
            f"Symbol '{symbol_name}' is defined in multiple files. "
            f"Caller context: '{caller_hint}'. "
            f"Which file most likely contains the implementation (not an interface/abstract)?\n"
            f"{candidate_list}\n"
            f"Reply with ONLY the number (e.g. '2'). No prose."
        )
        provider = get_provider()
        resp = await provider.chat(
            messages=[ChatMessage(role="user", content=prompt)],
            role=TaskRole.FAST,
            max_tokens=10,
        )
        idx = int(resp.content.strip()) - 1
        if 0 <= idx < len(candidates):
            return candidates[idx]
    except Exception:
        pass
    return None


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
