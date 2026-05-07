"""
entity_filter.py — Relevance filter applied after Stage 1 entity extraction.

Strips noise entities before they reach the LLM stages (intent synthesis,
relationship extraction, context synthesis, gap detection).

What gets filtered out:
  - Diff-artifact constants: "Added Constant:FOO", "Removed Constant:FOO"
    These come from git-diff fallback and represent field additions, not code logic.
  - Pure constants files: JsonKeyMapping, *Constants, *Keys, *Enums when
    they contain no methods (just static final fields).
  - Test classes: *Test, *Tests, *Spec, *IT — rarely relevant to endpoint logic.
  - Low-confidence extractions below the threshold.

What gets prioritised (kept even at lower confidence):
  - Controllers, Services, Repositories, Clients — core business logic.
  - DatabaseQuery entities — always relevant to understanding data access.
  - Entities whose file path matches a relevant-files top hit.

The filter is endpoint-aware: entities are scored by how closely their name /
file path relate to the target endpoint's URL segments.
"""

from __future__ import annotations

import re
from typing import Sequence

import structlog

from companybrain.models.entities import ExtractedEntity

log = structlog.get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

# Minimum confidence to retain an entity (diff-artifact constants are often 0.7
# but still noise — the type check handles them before confidence check).
_MIN_CONFIDENCE = 0.5

# Entity type priority: higher = more likely to survive the filter.
_TYPE_PRIORITY: dict[str, int] = {
    "ApiEndpoint":       10,
    "DatabaseQuery":     9,
    "Function":          8,
    "class":             7,
    "SchemaField":       4,
    "DatabaseColumn":    3,
    "constant":          1,
}

# Suffix patterns that indicate noise classes (pure data containers / mappings).
_NOISE_SUFFIXES = re.compile(
    r"(Constants?|Keys?|Enums?|Mapping|Mappings|Config|Properties|Messages?|Errors?)$",
    re.IGNORECASE,
)

# Prefix/suffix patterns for test classes.
_TEST_RE = re.compile(r"(Test|Tests|Spec|IT|IntegrationTest)$", re.IGNORECASE)

# Git-diff artifact names produced by the fallback entity extractor.
_DIFF_ARTIFACT_RE = re.compile(r"^(Added|Removed|Modified)\s+(Constant|Field|Import):", re.IGNORECASE)

# Layer-role keywords in file paths that signal high-value code.
_HIGH_VALUE_PATH_PARTS = frozenset({
    "controller", "controllers", "service", "services",
    "repository", "repositories", "repo", "repos",
    "handler", "handlers", "usecase", "usecases",
    "adapter", "adapters", "gateway", "gateways",
    "command", "query",  # CQRS
})


def filter_entities(
    entities: list[ExtractedEntity],
    endpoint: str,
    *,
    max_entities: int = 25,
) -> list[ExtractedEntity]:
    """
    Filter and rank entities by relevance to the endpoint.

    Returns at most `max_entities` entities, ordered by relevance score desc.
    Always logs a summary of what was kept vs dropped.
    """
    if not entities:
        return []

    endpoint_terms = _endpoint_terms(endpoint)
    scored: list[tuple[float, ExtractedEntity]] = []
    dropped_reasons: dict[str, int] = {}

    for entity in entities:
        reason = _drop_reason(entity)
        if reason:
            dropped_reasons[reason] = dropped_reasons.get(reason, 0) + 1
            continue

        score = _relevance_score(entity, endpoint_terms)
        scored.append((score, entity))

    # Sort by score descending, then confidence descending as tiebreak
    scored.sort(key=lambda t: (t[0], t[1].confidence), reverse=True)
    kept = [e for _, e in scored[:max_entities]]

    log.info(
        "Entity filter applied",
        before=len(entities),
        after=len(kept),
        dropped=len(entities) - len(kept),
        drop_reasons=dropped_reasons,
        top_entities=[e.name for e in kept[:5]],
    )
    return kept


# ── Internal helpers ──────────────────────────────────────────────────────────

def _drop_reason(entity: ExtractedEntity) -> str | None:
    """Return a reason string if the entity should be dropped, else None."""

    # 1. Diff-artifact constants ("Added Constant:FOO", "Removed Field:BAR")
    if _DIFF_ARTIFACT_RE.match(entity.name):
        return "diff_artifact"

    # 2. Pure constant entity type — field-level additions from git diffs (e.g.
    #    entity_type="Constant", name="ROLES"). These are diff noise, not business logic.
    if entity.entity_type.lower() in ("constant", "configkey"):
        return "constant_type"

    # 3. Low confidence
    if entity.confidence < _MIN_CONFIDENCE:
        return "low_confidence"

    # 4. Test classes — rarely relevant to production endpoint logic
    class_name = entity.name.split(".")[-1]
    if _TEST_RE.search(class_name):
        return "test_class"

    # 5. Pure noise-suffix classes (JsonKeyMapping, *Constants, *Keys, etc.)
    #    Only drop if entity_type suggests it's a holder class with no logic.
    if _NOISE_SUFFIXES.search(class_name) and entity.entity_type.lower() in ("class", "constant", ""):
        return "noise_suffix"

    return None


def _relevance_score(entity: ExtractedEntity, endpoint_terms: set[str]) -> float:
    """
    Score 0.0–10.0.  Higher = more relevant to the endpoint.

    Components:
      type_priority (0–10) weighted 0.4
      path_relevance (0–1) weighted 0.3   — file path contains high-value layer keywords
      name_match    (0–1) weighted 0.3   — entity name contains endpoint terms
    """
    type_score = _TYPE_PRIORITY.get(entity.entity_type, 2)

    path_parts = set(re.split(r"[/\\.]", entity.file.lower()))
    path_score = 1.0 if path_parts & _HIGH_VALUE_PATH_PARTS else 0.0

    name_lower = entity.name.lower()
    name_score = 1.0 if any(t in name_lower for t in endpoint_terms) else 0.0

    return type_score * 0.4 + path_score * 3.0 + name_score * 3.0


def _endpoint_terms(endpoint: str) -> set[str]:
    """
    Extract meaningful lowercase terms from the endpoint path.
    Drops generic segments: api, v1, v2, etc.
    """
    skip = {"api", "v1", "v2", "v3", "rest", "public", "private", "internal"}
    parts = re.split(r"[/\-_]", endpoint.lower())
    return {p for p in parts if p and p not in skip and not p.isdigit() and len(p) > 2}
