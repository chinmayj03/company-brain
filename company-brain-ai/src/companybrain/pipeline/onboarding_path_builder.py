"""
ADR-0059 Pass T2b — Onboarding curriculum builder.

For each DomainEntity produced by Pass T2 (or ADR-0055 SP-5), derive a small
curated reading order so the brain can answer "I'm new to this team. What
should I read first to understand X?" without guessing.

Picking strategy — purely heuristic, no LLM:
  1. Rank classes inside the DomainEntity by structural role:
       Controller / Endpoint / Resource → top of stack (entry)
       Service / Handler / UseCase      → middle of stack (logic)
       Repository / DAO / Mapper        → bottom of stack (data)
  2. Pick up to one of each role plus the largest "other" class.
  3. If the role buckets don't fill 3 slots, top up with whichever anchors
     remain to guarantee at least one class is returned.

We use the anchor class file paths (not the class names) so the answer
points the reader at concrete files. Where available, we also factor in the
TemporalOwnership.age_days so the recommended files lean toward older, more
canonical classes rather than churning new ones.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import structlog

from companybrain.models.entities import (
    EDGE_GUIDES,
    EDGE_READ_FIRST,
    DomainEntity,
    ExtractedEntity,
    ExtractedRelationship,
    OnboardingPath,
)

log = structlog.get_logger(__name__)


# Role buckets, ordered top-of-stack → bottom-of-stack so we present the
# reader with the entry point first.
_ROLE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("controller", ("controller", "endpoint", "resource", "handler")),
    ("service",    ("service", "usecase", "manager", "facade")),
    ("repository", ("repository", "dao", "mapper", "store")),
]


@dataclass
class OnboardingBuildResult:
    paths: list[OnboardingPath]
    edges: list[ExtractedRelationship]


def build_onboarding_paths(
    domains: Iterable[DomainEntity],
    entities: Iterable[ExtractedEntity],
    *,
    max_anchors_per_path: int = 5,
) -> OnboardingBuildResult:
    """Turn DomainEntity rows + their anchor classes into OnboardingPath rows.

    ``entities`` is the full entity list so we can look up each anchor URN's
    metadata (entity_type, name, temporal age) for ranking.
    """
    by_urn: dict[str, ExtractedEntity] = {e.external_id: e for e in entities}

    paths: list[OnboardingPath] = []
    edges: list[ExtractedRelationship] = []

    for domain in domains:
        anchors = [by_urn.get(u) for u in domain.anchor_class_urns]
        anchors = [a for a in anchors if a is not None]
        if not anchors:
            continue

        ordered = _pick_anchors(anchors, max_anchors=max_anchors_per_path)
        if not ordered:
            continue

        path = OnboardingPath(
            domain_name=domain.name,
            domain_urn=domain.external_id,
            anchor_class_urns=[a.external_id for a in ordered],
            rationale=(
                f"Read these {len(ordered)} files top-of-stack first to "
                f"understand the {domain.name} domain."
            ),
        )
        paths.append(path)
        edges.append(ExtractedRelationship(
            from_entity=path.external_id, from_type="OnboardingPath",
            edge_type=EDGE_GUIDES,
            to_entity=domain.external_id, to_type="DomainEntity",
            confidence=1.0,
            evidence=f"onboarding path for {domain.name}",
        ))
        for ord_idx, anchor in enumerate(ordered):
            edges.append(ExtractedRelationship(
                from_entity=path.external_id, from_type="OnboardingPath",
                edge_type=EDGE_READ_FIRST,
                to_entity=anchor.external_id, to_type=anchor.entity_type,
                confidence=1.0,
                evidence=f"step {ord_idx + 1} of {domain.name} onboarding",
            ))

    log.info(
        "onboarding_path_builder.build_onboarding_paths complete",
        domains_in=sum(1 for _ in domains),
        paths_out=len(paths),
        edges_out=len(edges),
    )
    return OnboardingBuildResult(paths=paths, edges=edges)


def project_onboarding_paths(paths: Iterable[OnboardingPath]) -> list[ExtractedEntity]:
    """Project OnboardingPath rows into ExtractedEntity projections so Stage
    5's writer treats them like every other entity (same pattern as
    ``project_risk_alerts`` and ``project_cross_file_entities``)."""
    out: list[ExtractedEntity] = []
    for p in paths:
        out.append(ExtractedEntity(
            entity_type="OnboardingPath",
            name=p.domain_name,
            file=f"_onboarding/{p.domain_name}",
            repo="_onboarding",
            signature=p.rationale[:200],
            last_modified_commit="",
            confidence=1.0,
            code_snippet=", ".join(p.anchor_class_urns),
        ))
    return out


# ── helpers ───────────────────────────────────────────────────────────────────

def _pick_anchors(
    anchors: list[ExtractedEntity],
    *,
    max_anchors: int,
) -> list[ExtractedEntity]:
    """Pick up to ``max_anchors`` classes, preferring one per structural role
    in the order Controller → Service → Repository, then largest "other"."""
    by_role: dict[str, list[ExtractedEntity]] = {role: [] for role, _ in _ROLE_KEYWORDS}
    unrouted: list[ExtractedEntity] = []
    for a in anchors:
        role = _classify_role(a.name)
        if role is None:
            unrouted.append(a)
        else:
            by_role[role].append(a)

    # Within each role, prefer older (more canonical) classes — falls back to
    # anchor order when temporal data is missing.
    for role, items in by_role.items():
        items.sort(key=_age_key, reverse=True)
    unrouted.sort(key=_age_key, reverse=True)

    ordered: list[ExtractedEntity] = []
    for role, _ in _ROLE_KEYWORDS:
        items = by_role[role]
        if items:
            ordered.append(items.pop(0))
        if len(ordered) >= max_anchors:
            return ordered

    # Top up with whatever's left, role-buckets first then unrouted.
    leftovers: list[ExtractedEntity] = []
    for items in by_role.values():
        leftovers.extend(items)
    leftovers.extend(unrouted)
    leftovers.sort(key=_age_key, reverse=True)

    for a in leftovers:
        if len(ordered) >= max_anchors:
            break
        if a not in ordered:
            ordered.append(a)
    return ordered


def _classify_role(class_name: str) -> str | None:
    lname = class_name.lower()
    for role, keywords in _ROLE_KEYWORDS:
        if any(kw in lname for kw in keywords):
            return role
    return None


def _age_key(entity: ExtractedEntity) -> int:
    """Age in days when ``temporal`` is populated, else 0 (newest)."""
    if entity.temporal is None:
        return 0
    return entity.temporal.age_days
