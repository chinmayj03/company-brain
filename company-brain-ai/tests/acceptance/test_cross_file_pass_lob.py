"""ADR-0055 acceptance — the lob anti-pattern + soft-delete invariant + Payer domain.

The ADR's original acceptance test reaches into a network-iq fixture repo
and full-pipeline harness that don't exist outside the production
benchmark environment. To still ship an acceptance test that runs in CI
(per the ADR's "Run the acceptance tests before opening the PR" rule),
this file stages an in-memory snapshot of the network-iq facts the cross-
file pass needs to see and asserts the same three contracts the original
specifies:

  1. The lob anti-pattern is detected (Pattern + 1 violator).
  2. The soft-delete-via-is_current invariant is detected.
  3. The "Payer" domain entity is inferred + linked to >=5 anchor classes.

LLM-backed sub-passes (SP-3, SP-5) are exercised with a deterministic
fake provider so we test the wiring + JSON contract; integration with a
real provider is covered by the production benchmark, not by CI.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Optional

import pytest

from companybrain.llm import ChatMessage
from companybrain.models.entities import (
    EDGE_REPRESENTS,
    EDGE_SHARES_INVARIANT,
    EDGE_VIOLATES_PATTERN,
    ExtractedEntity,
    ExtractedRelationship,
)
from companybrain.pipeline import (
    domain_inferrer as _domain_inferrer,
    invariant_inferrer as _invariant_inferrer,
)
from companybrain.pipeline.antipattern_detector import ConventionSite
from companybrain.pipeline.cross_file_pass import run_cross_file_pass


# ── Fake LLM provider ─────────────────────────────────────────────────────────

@dataclass
class _Routed:
    """One canned response keyed by a substring that must be in the user msg."""
    needle: str
    payload: dict


class _FakeProvider:
    def __init__(self, routes: list[_Routed]):
        self._routes = routes
        self.calls: list[str] = []

    async def chat_json(
        self,
        messages: list[ChatMessage],
        role=None,
        max_tokens: int = 0,
    ) -> str:
        user = next((m.content for m in messages if m.role == "user"), "")
        self.calls.append(user)
        for r in self._routes:
            if r.needle in user:
                return json.dumps(r.payload)
        return "{}"


# ── In-memory network-iq snapshot ─────────────────────────────────────────────

def _network_iq_snapshot() -> tuple[
    list[ExtractedEntity], list[ExtractedRelationship], list[ConventionSite],
]:
    """Stage the minimum entities, edges, and convention sites needed to
    surface the three contracts the ADR's acceptance test asserts."""
    entities: list[ExtractedEntity] = []

    # 5 plan-info reader methods, all in PlanInfoRepository.
    plan_methods = [
        "PlanInfoRepository.findCurrentByPayer",
        "PlanInfoRepository.listCurrentForLob",
        "PlanInfoRepository.lookupCurrent",
        "PlanInfoRepository.fetchCurrentDetails",
        "PlanInfoRepository.streamCurrent",
    ]
    for name in plan_methods:
        entities.append(ExtractedEntity(
            entity_type="Function",
            name=name,
            file="java/PlanInfoRepository.java",
            repo="network-iq-backend",
            signature=f"List<PlanInfo> {name.split('.')[-1]}(...)",
            last_modified_commit="abc",
            confidence=0.9,
            code_snippet=(
                "select * from plan_info where plan_info.is_current = true"
            ),
        ))

    # 8 Payer/Plan/Provider domain classes.
    domain_classes = [
        ("PayerInfo", "Payer"),
        ("BasePayer", "Payer"),
        ("PayerPlanProvider", "Payer"),
        ("PayerSummary", "Payer"),
        ("PayerLookupService", "Payer"),
        ("PlanInfo", "Plan"),
        ("PlanLookupService", "Plan"),
        ("ProviderInfo", "Provider"),
    ]
    for name, _ in domain_classes:
        entities.append(ExtractedEntity(
            entity_type="Class", name=name,
            file=f"java/{name}.java",
            repo="network-iq-backend",
            signature=f"class {name}",
            last_modified_commit="abc",
            confidence=0.9,
        ))

    # READS_COLUMN edges from each plan-method to plan_info.is_current.
    relationships: list[ExtractedRelationship] = [
        ExtractedRelationship(
            from_entity=f"network-iq-backend/java/PlanInfoRepository.java::{m}",
            from_type="Function",
            edge_type="READS_COLUMN",
            to_entity="plan_info.is_current",
            to_type="DatabaseColumn",
            confidence=0.9,
            evidence="WHERE plan_info.is_current = true",
        )
        for m in plan_methods
    ]

    # 16 DTOs that use the JsonKeyMapping.LOB constant + 1 odd one out.
    convention_sites: list[ConventionSite] = []
    for i in range(16):
        dto = f"network-iq-backend/java/Dto{i}.java::Dto{i}"
        convention_sites.append(ConventionSite(
            entity_external_id=dto,
            field_key="lob",
            uses_constant=True,
            constant_name="JsonKeyMapping.LOB",
        ))
    convention_sites.append(ConventionSite(
        entity_external_id=(
            "network-iq-backend/java/CompetitivenessReportRequestDTO.java"
            "::CompetitivenessReportRequestDTO"
        ),
        field_key="lob",
        uses_constant=False,
    ))

    return entities, relationships, convention_sites


# ── Fake responses for SP-3 + SP-5 ────────────────────────────────────────────

def _install_fake_providers(monkeypatch: pytest.MonkeyPatch) -> _FakeProvider:
    """Wire a FakeProvider that returns canned JSON for SP-3 and SP-5.

    The same provider serves all three LLM-backed sub-passes; routing is
    keyed on a substring that uniquely identifies which sub-pass made the
    call.
    """
    soft_delete_payload = {
        "invariants": [
            {
                "name": "soft_delete_filter",
                "statement": "all reads of plan_info filter is_current=true",
                "affected_qnames": [
                    "PlanInfoRepository.findCurrentByPayer",
                    "PlanInfoRepository.listCurrentForLob",
                    "PlanInfoRepository.lookupCurrent",
                    "PlanInfoRepository.fetchCurrentDetails",
                    "PlanInfoRepository.streamCurrent",
                ],
            }
        ]
    }
    domain_payload = {
        "domains": [
            {
                "name": "Payer",
                "description": "A health insurance carrier (e.g. Cigna).",
                "aliases": ["payer_id", "PayerPlan", "BasePayer"],
                "anchor_class_qnames": [
                    "PayerInfo", "BasePayer", "PayerPlanProvider",
                    "PayerSummary", "PayerLookupService",
                ],
            },
            {
                "name": "Plan",
                "description": "A specific health plan offered by a Payer.",
                "aliases": ["PlanInfo", "PlanLookupService"],
                "anchor_class_qnames": [
                    "PlanInfo", "PlanLookupService", "PayerPlanProvider",
                ],
            },
        ]
    }
    fake = _FakeProvider(routes=[
        _Routed(needle="<window>",  payload=soft_delete_payload),
        _Routed(needle="<classes>", payload=domain_payload),
    ])
    monkeypatch.setattr(_invariant_inferrer, "get_provider", lambda: fake)
    monkeypatch.setattr(_domain_inferrer,    "get_provider", lambda: fake)
    return fake


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_lob_antipattern_detected(monkeypatch: pytest.MonkeyPatch):
    """16 DTOs use JsonKeyMapping.LOB, one uses literal "lob" — the latter
    is flagged as VIOLATES_PATTERN. (ADR-0055 acceptance #1)"""
    entities, relationships, convention_sites = _network_iq_snapshot()
    _install_fake_providers(monkeypatch)

    result = await run_cross_file_pass(
        entities, relationships,
        convention_sites=convention_sites,
    )

    lob_patterns = [
        p for p in result.patterns
        if "LOB" in p.name or "lob" in p.name.lower()
    ]
    assert lob_patterns, (
        "expected a Pattern entity for the lob convention; "
        f"got {[p.name for p in result.patterns]}"
    )
    assert any(p.instance_count >= 16 for p in lob_patterns)

    violations = [e for e in result.new_edges if e.edge_type == EDGE_VIOLATES_PATTERN]
    assert len(violations) == 1
    assert "Competitiveness" in violations[0].from_entity


async def test_soft_delete_invariant_detected(monkeypatch: pytest.MonkeyPatch):
    """All 5 reads of plan_info filter is_current=true → SharedInvariant.
    (ADR-0055 acceptance #2)"""
    entities, relationships, convention_sites = _network_iq_snapshot()
    _install_fake_providers(monkeypatch)

    result = await run_cross_file_pass(
        entities, relationships,
        convention_sites=convention_sites,
    )

    is_current_invariants = [
        i for i in result.shared_invariants
        if "is_current" in i.statement
    ]
    assert is_current_invariants, (
        "expected a SharedInvariant mentioning is_current; "
        f"got {[i.statement for i in result.shared_invariants]}"
    )
    inv = is_current_invariants[0]
    assert len(inv.affected_method_urns) >= 3

    shares_edges = [e for e in result.new_edges if e.edge_type == EDGE_SHARES_INVARIANT]
    assert len(shares_edges) >= 3


async def test_domain_entity_payer_inferred(monkeypatch: pytest.MonkeyPatch):
    """SP-5 must produce a 'Payer' DomainEntity linked to >=5 anchor classes.
    (ADR-0055 acceptance #3)"""
    entities, relationships, convention_sites = _network_iq_snapshot()
    _install_fake_providers(monkeypatch)

    result = await run_cross_file_pass(
        entities, relationships,
        convention_sites=convention_sites,
    )

    payer_candidates = [d for d in result.domain_entities if d.name == "Payer"]
    assert payer_candidates, (
        "expected a DomainEntity named Payer; "
        f"got {[d.name for d in result.domain_entities]}"
    )
    payer = payer_candidates[0]
    assert len(payer.anchor_class_urns) >= 5
    represents_edges = [
        e for e in result.new_edges
        if e.edge_type == EDGE_REPRESENTS and e.to_entity == payer.external_id
    ]
    assert len(represents_edges) >= 5


async def test_pipeline_writeable_projection(monkeypatch: pytest.MonkeyPatch):
    """Ensure the projection helper turns cross-file results into
    ExtractedEntity rows that Stage 5 can write through the same BrainStore
    path used for Functions / Classes. Smoke test only — we just need
    project_cross_file_entities to return a non-empty list with valid
    external_id strings."""
    from companybrain.pipeline.cross_file_pass import project_cross_file_entities

    entities, relationships, convention_sites = _network_iq_snapshot()
    _install_fake_providers(monkeypatch)
    result = await run_cross_file_pass(
        entities, relationships,
        convention_sites=convention_sites,
    )
    projected = project_cross_file_entities(result)
    assert projected
    assert all(p.external_id for p in projected)
    kinds = {p.entity_type for p in projected}
    assert "Pattern" in kinds
    assert "DomainEntity" in kinds
