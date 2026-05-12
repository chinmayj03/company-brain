"""
ADR-0055 Stage 2.5 — Cross-File Cross-Cutting Pass orchestrator.

Sequences SP-1 → SP-5 against the entity + relationship graph produced by
Stage 2 and returns the resulting Patterns, SharedInvariants,
DomainEntities, ImplicitContracts, and the new edges that connect them
back to existing entities.

The pass is ALWAYS safe to call:
  - empty input             → empty result, no LLM call
  - LLM call fails inside SP-3/4/5 → those sub-results come back empty;
                              SP-1/2 (deterministic) still contribute.
  - convention_sites omitted → SP-2 only does the strength-flip shape.

Caller (orchestrator.py) is responsible for merging the resulting edges
into the relationship list and feeding the new entities to BrainStore in
Stage 5.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

import structlog

from companybrain.config import settings
from companybrain.models.entities import (
    DomainEntity,
    ExtractedEntity,
    ExtractedRelationship,
    ImplicitContract,
    Pattern,
    SharedInvariant,
)
from companybrain.pipeline.antipattern_detector import (
    AntipatternResult,
    ConventionSite,
    detect_antipatterns,
)
from companybrain.pipeline.domain_inferrer import (
    DomainInferenceResult,
    infer_domain_entities,
)
from companybrain.pipeline.idiom_detector import detect_idioms
from companybrain.pipeline.invariant_inferrer import (
    InvariantInferenceResult,
    infer_implicit_contracts,
    infer_shared_invariants,
)

log = structlog.get_logger(__name__)


@dataclass
class CrossFilePassResult:
    patterns:           list[Pattern]            = field(default_factory=list)
    shared_invariants:  list[SharedInvariant]    = field(default_factory=list)
    domain_entities:    list[DomainEntity]       = field(default_factory=list)
    implicit_contracts: list[ImplicitContract]   = field(default_factory=list)
    new_edges:          list[ExtractedRelationship] = field(default_factory=list)

    @property
    def summary(self) -> dict:
        return {
            "patterns":           len(self.patterns),
            "shared_invariants":  len(self.shared_invariants),
            "domain_entities":    len(self.domain_entities),
            "implicit_contracts": len(self.implicit_contracts),
            "new_edges":          len(self.new_edges),
        }


async def run_cross_file_pass(
    entities: Iterable[ExtractedEntity],
    relationships: Iterable[ExtractedRelationship],
    *,
    convention_sites: Optional[Iterable[ConventionSite]] = None,
    candidate_universe: Optional[dict[str, list[str]]] = None,
    enable_llm_passes: Optional[bool] = None,
) -> CrossFilePassResult:
    """Run SP-1 → SP-5. See module docstring for behaviour guarantees."""
    entities = list(entities)
    relationships = list(relationships)
    out = CrossFilePassResult()
    if not entities and not relationships:
        return out

    min_instances     = int(getattr(settings, "cross_file_pattern_min_instances",   5))
    min_strength      = float(getattr(settings, "cross_file_antipattern_min_strength", 0.80))
    window_size       = int(getattr(settings, "cross_file_invariant_window_size",   8))
    enable_llm        = (
        bool(getattr(settings, "cross_file_enable_llm_passes", True))
        if enable_llm_passes is None else bool(enable_llm_passes)
    )

    # ── SP-1: deterministic idioms ─────────────────────────────────────────
    sp1 = detect_idioms(relationships, min_instances=min_instances)
    out.patterns.extend(sp1.patterns)
    out.new_edges.extend(sp1.edges)

    # ── SP-2: anti-patterns + convention violations ────────────────────────
    sp2: AntipatternResult = detect_antipatterns(
        patterns=out.patterns,
        candidate_universe=candidate_universe,
        convention_sites=list(convention_sites) if convention_sites else None,
        min_strength=min_strength,
        min_population=min_instances,
    )
    out.patterns.extend(sp2.patterns)
    out.new_edges.extend(sp2.edges)

    # ── SP-3: cross-method invariants (LLM) ────────────────────────────────
    if enable_llm:
        sp3: InvariantInferenceResult = await infer_shared_invariants(
            entities, window_size=window_size,
        )
        out.shared_invariants.extend(sp3.invariants)
        out.new_edges.extend(sp3.edges)

        # ── SP-4: implicit contracts (LLM) ─────────────────────────────────
        sp4: InvariantInferenceResult = await infer_implicit_contracts(
            entities, relationships,
        )
        out.implicit_contracts.extend(sp4.contracts)
        out.new_edges.extend(sp4.edges)

        # ── SP-5: domain entity inference (LLM) ────────────────────────────
        sp5: DomainInferenceResult = await infer_domain_entities(entities)
        out.domain_entities.extend(sp5.domains)
        out.new_edges.extend(sp5.edges)

    log.info(
        "cross_file_pass.run_cross_file_pass complete",
        entities=len(entities),
        relationships=len(relationships),
        **out.summary,
    )
    return out


def project_cross_file_entities(result: CrossFilePassResult) -> list[ExtractedEntity]:
    """Project Pattern / SharedInvariant / DomainEntity into ExtractedEntity rows.

    Stage 5 (graph population) writes ``ExtractedEntity``s through
    ``_to_brain_entity``. The cross-file pass emits its own typed
    dataclasses; this helper lays them down as lightweight ExtractedEntity
    projections so they round-trip through the same writer with an
    appropriate entity_type. Pure function — no I/O, no LLM.
    """
    out: list[ExtractedEntity] = []
    for p in result.patterns:
        out.append(ExtractedEntity(
            entity_type="Pattern",
            name=p.name,
            file=f"_cross_file/patterns/{p.name}",
            repo="_cross_file",
            signature=p.description[:200],
            last_modified_commit="",
            confidence=p.confidence,
            code_snippet=p.description,
        ))
    for inv in result.shared_invariants:
        out.append(ExtractedEntity(
            entity_type="SharedInvariant",
            name=inv.name,
            file=f"_cross_file/invariants/{inv.name}",
            repo="_cross_file",
            signature=inv.statement[:200],
            last_modified_commit="",
            confidence=inv.confidence,
            code_snippet=inv.statement,
        ))
    for d in result.domain_entities:
        out.append(ExtractedEntity(
            entity_type="DomainEntity",
            name=d.name,
            file=f"_cross_file/domains/{d.name}",
            repo="_cross_file",
            signature=d.description[:200],
            last_modified_commit="",
            confidence=d.confidence,
            code_snippet=d.description,
        ))
    return out
