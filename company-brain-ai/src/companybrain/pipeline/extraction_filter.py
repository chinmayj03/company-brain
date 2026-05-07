"""
ExtractionFilter — classifies files into extraction tiers.

Implements the directive's frugality hierarchy per file:
  Tier 1: Deterministic — tree-sitter can extract all facts (no LLM needed)
  Tier 2: Pattern match — semgrep/regex finds key structures
  Tier 3: Heuristic — naming conventions + structure hints
  Tier 5: Small LLM (Haiku) — ambiguous cases
  Tier 6: Large LLM (Sonnet) — complex multi-step reasoning

Decision rules:
  - Test files → skip (never extract)
  - Config files (application.yml, .properties) → tier 1 (regex key extraction)
  - Interface-only files → tier 1 (signatures only, no bodies to analyze)
  - Boilerplate DTOs/entities → tier 2 (field extraction via AST, no LLM)
  - Complex service/controller/repository → tier 5 (Haiku)
  - High-stakes synthesis (business context, drift) → tier 6 (Sonnet)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
import structlog

from companybrain.pipeline.file_walker import FileInfo

log = structlog.get_logger(__name__)

ExtractionTier = Literal["skip", "tier1", "tier2", "tier3", "tier5", "tier6"]


@dataclass
class FileClassification:
    file_info: FileInfo
    tier: ExtractionTier
    reason: str
    estimated_entities: int = 0   # rough estimate for budget planning


# Patterns for files that are always test files
_TEST_PATTERNS: list[re.Pattern] = [
    re.compile(r'(Test|Tests|Spec|IT|Mock|Stub|Fake)\.(java|kt|py|ts|js)$'),
    re.compile(r'__(test|spec)__'),
    re.compile(r'\.(test|spec)\.(ts|js|tsx|jsx)$'),
    re.compile(r'test_\w+\.py$'),
]

# Patterns for pure-interface / pure-DTO files (tier 1 — AST extracts all facts)
_INTERFACE_PATTERNS: list[re.Pattern] = [
    re.compile(r'(Repository|DAO|Port|Gateway)\.(java|kt)$'),  # Spring Data interfaces
    re.compile(r'(DTO|Request|Response|Payload|Model)\.(java|kt|ts)$'),
    re.compile(r'(Entity|Record)\.(java|kt)$'),
]

# Config files — tier 1 regex extraction
_CONFIG_PATTERNS: list[re.Pattern] = [
    re.compile(r'application\.(yml|yaml|properties)$'),
    re.compile(r'bootstrap\.(yml|yaml|properties)$'),
    re.compile(r'(logback|log4j)\.(xml|yml)$'),
    re.compile(r'(Dockerfile|docker-compose.*\.yml)$'),
]


class ExtractionFilter:
    """
    Classifies a file into an extraction tier before any work is done.
    Frugality: most decisions are made from filename + extension alone.
    """

    def classify(self, file_info: FileInfo) -> FileClassification:
        """Classify a single file."""
        path = file_info.path
        name = path.name

        # Always skip non-extractable
        if not file_info.should_extract:
            return FileClassification(file_info, "skip", file_info.skip_reason)

        # Test files → always skip
        if any(pat.search(name) for pat in _TEST_PATTERNS):
            return FileClassification(file_info, "skip", "test_file")

        # Config files → tier 1 (regex key/value extraction, no LLM)
        if any(pat.search(name) for pat in _CONFIG_PATTERNS):
            return FileClassification(file_info, "tier1", "config_file")

        # Pure interface / DTO → tier 1 (AST signature extraction, no LLM body)
        if any(pat.search(name) for pat in _INTERFACE_PATTERNS):
            return FileClassification(file_info, "tier1", "interface_or_dto",
                                      estimated_entities=_estimate_entities(file_info))

        # Small files (< 5KB) — likely boilerplate or utility → tier 2
        if file_info.size_bytes < 5_000:
            return FileClassification(file_info, "tier2", "small_file",
                                      estimated_entities=_estimate_entities(file_info))

        # Controller / Service / Repository impl — main extraction → tier 5 (Haiku)
        tier5_hints = ("Controller", "Service", "ServiceImpl", "RepositoryImpl",
                       "Resource", "Handler", "Endpoint")
        if any(h in name for h in tier5_hints):
            return FileClassification(file_info, "tier5", "controller_service_repository",
                                      estimated_entities=_estimate_entities(file_info))

        # Default: tier 5 for known languages, tier 3 for others
        if file_info.language in ("java", "kotlin", "python", "typescript", "javascript", "go"):
            return FileClassification(file_info, "tier5", "default_extractable",
                                      estimated_entities=_estimate_entities(file_info))

        return FileClassification(file_info, "tier3", "unknown_language")

    def classify_batch(self, files: list[FileInfo]) -> list[FileClassification]:
        """Classify multiple files and log tier distribution."""
        results = [self.classify(f) for f in files]

        tier_counts: dict[str, int] = {}
        for r in results:
            tier_counts[r.tier] = tier_counts.get(r.tier, 0) + 1

        log.info(
            "ExtractionFilter classification complete",
            total=len(results),
            **{f"tier_{k}": v for k, v in tier_counts.items()},
        )
        return results


def _estimate_entities(file_info: FileInfo) -> int:
    """Rough estimate of entity count from file size."""
    kb = file_info.size_bytes / 1024
    if kb < 5:   return 3
    if kb < 20:  return 8
    if kb < 50:  return 15
    return 25
