"""ADR-0059 acceptance — bus_factor alert, Payer domain, onboarding curriculum.

Like the ADR-0055 acceptance harness, this file stages an in-memory snapshot
of the network-iq facts the passes need to see — running the full pipeline
against a real git repo + LLM is what the production benchmark covers, not
what CI is responsible for. We assert three contracts the ADR-0059 spec
calls out:

  1. Pass T1 + RiskAlertDetector flag ``CompetitivenessPlanRepository`` as a
     bus_factor_one risk when Sarah owns 80% of the lines.
  2. Pass T2 produces a "Payer" DomainEntity linked to >=5 anchor classes
     (richer than SP-5's input since T2 also sees endpoints + tables).
  3. Pass T2b builds an OnboardingPath for the Competitiveness domain whose
     anchors include CompetitivenessController + CompetitivenessPlanRepository
     plus the service interface — matching benchmark question C4.

LLM-backed bits (T2's one-shot domain call) run against a deterministic fake
provider so the contract + JSON parsing is exercised, not the LLM itself.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest

from companybrain.llm import ChatMessage
from companybrain.models.entities import (
    DomainEntity,
    EDGE_AFFECTS,
    EDGE_GUIDES,
    EDGE_REPRESENTS,
    ExtractedEntity,
    TemporalOwnership,
)
from companybrain.pipeline import (
    domain_inference_pass as _domain_inference_pass,
    git_blame_aggregator as _blame,
)
from companybrain.pipeline.onboarding_path_builder import build_onboarding_paths
from companybrain.pipeline.risk_alert_detector import detect_risk_alerts
from companybrain.pipeline.temporal_pass import run_temporal_pass


# ── Fake LLM provider (mirrors ADR-0055's pattern) ────────────────────────────

@dataclass
class _Routed:
    needle: str
    payload: dict


class _FakeProvider:
    def __init__(self, routes: list[_Routed]):
        self._routes = routes
        self.calls: list[str] = []

    async def chat_json(self, messages, role=None, max_tokens: int = 0) -> str:
        user = next((m.content for m in messages if m.role == "user"), "")
        self.calls.append(user)
        for r in self._routes:
            if r.needle in user:
                return json.dumps(r.payload)
        return "{}"


# ── Network-iq in-memory snapshot ─────────────────────────────────────────────

_DOMAIN_CLASSES = [
    ("PayerInfo",         "Payer"),
    ("BasePayer",         "Payer"),
    ("PayerPlanProvider", "Payer"),
    ("PayerSummary",      "Payer"),
    ("PayerLookupService","Payer"),
    ("PlanInfo",          "Plan"),
    ("PlanLookupService", "Plan"),
    ("ProviderInfo",      "Provider"),
]

_COMPETITIVENESS_CLASSES = [
    ("CompetitivenessController",    "controller"),
    ("CompetitivenessServicePort",   "service"),  # service interface, port/in
    ("CompetitivenessPlanRepository","repository"),
    ("CompetitivenessSummaryDTO",    "other"),
]


def _network_iq_snapshot() -> list[ExtractedEntity]:
    entities: list[ExtractedEntity] = []
    for name, _ in _DOMAIN_CLASSES:
        entities.append(ExtractedEntity(
            entity_type="Class", name=name,
            file=f"java/{name}.java",
            repo="network-iq-backend",
            signature=f"class {name}", last_modified_commit="abc",
            confidence=0.9,
        ))
    for name, _ in _COMPETITIVENESS_CLASSES:
        entities.append(ExtractedEntity(
            entity_type="Class", name=name,
            file=f"java/port/in/{name}.java" if name.endswith("Port") else f"java/{name}.java",
            repo="network-iq-backend",
            signature=f"class {name}", last_modified_commit="abc",
            confidence=0.9,
        ))
    # A couple of API endpoints + DB tables so Pass T2's prompt is richer
    # than SP-5's. The Payer domain answer is what matters but the prompt
    # shape change is what makes this PASS T2 (vs SP-5).
    entities.append(ExtractedEntity(
        entity_type="ApiEndpoint", name="GET /competitiveness/{payerId}",
        file="java/CompetitivenessController.java",
        repo="network-iq-backend",
        signature="getPayerCompetitiveness(payerId)", last_modified_commit="abc",
        confidence=0.9,
    ))
    entities.append(ExtractedEntity(
        entity_type="DatabaseTable", name="plan_info",
        file="db/migrations/V01__init.sql",
        repo="network-iq-backend",
        signature="plan_info(payer_id, plan_id, is_current)",
        last_modified_commit="abc",
        confidence=0.9,
    ))
    return entities


# ── Fake blame data for the LobRepository ─────────────────────────────────────

def _install_fake_blame(monkeypatch: pytest.MonkeyPatch):
    """Stub out blame_file/file_commits so Pass T1 sees Sarah owning 80% of
    CompetitivenessPlanRepository, with a sprinkle of churn on a sibling.
    """

    def fake_blame(repo_root, rel_path: str):
        if rel_path.endswith("CompetitivenessPlanRepository.java"):
            # 85/8/7 — primary > 70%, runner-up < 10% → bus_factor_one fires.
            return [
                *(_make_line(i, "Sarah") for i in range(1, 86)),
                *(_make_line(i, "Bob")    for i in range(86, 94)),
                *(_make_line(i, "Alex")   for i in range(94, 101)),
            ]
        if rel_path.endswith("CompetitivenessController.java"):
            # Even split: 29 / 30 / 40 — no risk alert.
            return [
                *(_make_line(i, "Sarah") for i in range(1, 30)),
                *(_make_line(i, "Bob")    for i in range(30, 60)),
                *(_make_line(i, "Alex")   for i in range(60, 100)),
            ]
        # Default for unrelated files: roughly balanced so no spurious alerts
        # leak from unmocked Payer/Plan/Provider DTOs.
        return [
            *(_make_line(i, "Sarah") for i in range(1, 20)),
            *(_make_line(i, "Bob")    for i in range(20, 40)),
            *(_make_line(i, "Alex")   for i in range(40, 60)),
        ]

    def fake_commits(repo_root, rel_path: str):
        if rel_path.endswith("CompetitivenessPlanRepository.java"):
            return [
                _make_touch("s1", "Sarah", days_ago=2),
                _make_touch("s2", "Sarah", days_ago=15),
                _make_touch("s3", "Sarah", days_ago=200),
            ]
        return [_make_touch("c0", "Anon", days_ago=400)]

    monkeypatch.setattr(_blame, "blame_file",  fake_blame)
    monkeypatch.setattr(_blame, "file_commits", fake_commits)


def _make_line(line_no: int, author: str) -> _blame.BlameLine:
    return _blame.BlameLine(
        line_no=line_no, author=author, author_mail=author.lower() + "@n",
        commit_sha="deadbeef" * 5,
        commit_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
    )


def _make_touch(sha: str, author: str, *, days_ago: int) -> _blame.CommitTouch:
    return _blame.CommitTouch(
        sha=sha, author=author, author_mail=author.lower() + "@n",
        timestamp=datetime.now(tz=timezone.utc) - timedelta(days=days_ago),
    )


def _install_fake_t2_provider(monkeypatch: pytest.MonkeyPatch) -> _FakeProvider:
    """Wire a fake provider for the Pass T2 LLM call. The needle is ``<classes>``
    — same shape the prompt uses — so routing matches the real prompt."""
    payload = {
        "domains": [
            {"name": "Payer",
             "description": "Health insurance carrier.",
             "aliases": ["payer", "payer_id", "PayerPlan", "BasePayer"],
             "anchor_class_qnames": [
                 "PayerInfo", "BasePayer", "PayerPlanProvider",
                 "PayerSummary", "PayerLookupService",
             ],
             "cross_concept_relationships": [
                 "Plan belongs to Payer (one-to-many)",
             ]},
            {"name": "Plan",
             "description": "A health plan offered by a Payer.",
             "aliases": ["PlanInfo", "PlanLookupService"],
             "anchor_class_qnames": [
                 "PlanInfo", "PlanLookupService", "PayerPlanProvider",
             ]},
            {"name": "Competitiveness",
             "description": "Competitive-analysis feature surface.",
             "aliases": ["competitiveness", "competitor"],
             "anchor_class_qnames": [
                 "CompetitivenessController",
                 "CompetitivenessServicePort",
                 "CompetitivenessPlanRepository",
                 "CompetitivenessSummaryDTO",
             ]},
        ]
    }
    fake = _FakeProvider(routes=[_Routed(needle="<classes>", payload=payload)])
    monkeypatch.setattr(_domain_inference_pass, "get_provider", lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _clear_blame_cache():
    _blame.clear_cache()
    yield
    _blame.clear_cache()


# ── Acceptance tests ──────────────────────────────────────────────────────────

async def test_bus_factor_alert_for_lob_path(monkeypatch: pytest.MonkeyPatch):
    """If git history shows Sarah wrote 80% of CompetitivenessPlanRepository,
    a bus_factor_one RiskAlert must be emitted. (ADR-0059 acceptance #1)"""
    entities = _network_iq_snapshot()
    _install_fake_blame(monkeypatch)

    entities, stats = await run_temporal_pass(
        entities, repo_resolver=lambda _: Path("/tmp/fake-root"),
    )
    assert stats.entities_blamed > 0

    alerts, edges = detect_risk_alerts(entities)
    bus_alerts = [a for a in alerts if a.kind == "bus_factor_one"]
    assert bus_alerts, (
        "expected at least one bus_factor_one RiskAlert; "
        f"got {[a.kind for a in alerts]}"
    )
    assert any(
        "CompetitivenessPlanRepository" in a.affected_entity_urn
        for a in bus_alerts
    ), (
        "expected the alert to point at CompetitivenessPlanRepository; "
        f"got {[a.affected_entity_urn for a in bus_alerts]}"
    )
    # And there's an AFFECTS edge from the alert to the entity.
    affects = [e for e in edges if e.edge_type == EDGE_AFFECTS]
    assert affects, "expected AFFECTS edges from RiskAlerts to entities"


async def test_domain_entity_payer_inferred(monkeypatch: pytest.MonkeyPatch):
    """Pass T2 produces a 'Payer' DomainEntity with >=5 anchor classes.
    (ADR-0059 acceptance #2)"""
    entities = _network_iq_snapshot()
    _install_fake_t2_provider(monkeypatch)
    from companybrain.pipeline.domain_inference_pass import run_domain_inference_pass

    result = await run_domain_inference_pass(entities)
    payer = next((d for d in result.domains if d.name == "Payer"), None)
    assert payer is not None, (
        "expected a DomainEntity named Payer; "
        f"got {[d.name for d in result.domains]}"
    )
    assert len(payer.anchor_class_urns) >= 5
    assert "payer_id" in [a.lower() for a in payer.aliases]
    represents = [
        e for e in result.edges
        if e.edge_type == EDGE_REPRESENTS and e.to_entity == payer.external_id
    ]
    assert len(represents) >= 5


async def test_onboarding_path_for_new_hire(monkeypatch: pytest.MonkeyPatch):
    """C4 in benchmark — onboarding curriculum must reference the controller,
    the service interface, and the repository. (ADR-0059 acceptance #3)"""
    entities = _network_iq_snapshot()
    fake = _install_fake_t2_provider(monkeypatch)

    from companybrain.pipeline.domain_inference_pass import run_domain_inference_pass

    t2 = await run_domain_inference_pass(entities)
    onboarding = build_onboarding_paths(t2.domains, entities)
    comp = next(
        (p for p in onboarding.paths if p.domain_name == "Competitiveness"),
        None,
    )
    assert comp is not None, (
        "expected an OnboardingPath for the Competitiveness domain; "
        f"got {[p.domain_name for p in onboarding.paths]}"
    )
    names = [u.split("::")[-1] for u in comp.anchor_class_urns]
    assert "CompetitivenessController"    in names
    assert "CompetitivenessPlanRepository" in names
    # Service interface stored under port/in — appears with the "Service" role
    # because it ends with ServicePort.
    assert any(n.endswith("ServicePort") or "Service" in n for n in names)

    # GUIDES edge from the onboarding path to the domain it serves.
    assert any(e.edge_type == EDGE_GUIDES for e in onboarding.edges)


async def test_pipeline_projection_smoke(monkeypatch: pytest.MonkeyPatch):
    """Stage-5 projection helpers must emit ExtractedEntity rows that
    Stage 5's BrainStore can write. Mirror of cross_file_pass's smoke test."""
    from companybrain.pipeline.domain_inference_pass import (
        project_domain_entities, run_domain_inference_pass,
    )
    from companybrain.pipeline.onboarding_path_builder import project_onboarding_paths
    from companybrain.pipeline.risk_alert_detector import project_risk_alerts

    entities = _network_iq_snapshot()
    _install_fake_blame(monkeypatch)
    _install_fake_t2_provider(monkeypatch)

    entities, _ = await run_temporal_pass(
        entities, repo_resolver=lambda _: Path("/tmp/fake-root"),
    )
    alerts, _ = detect_risk_alerts(entities)
    t2 = await run_domain_inference_pass(entities)
    onboarding = build_onboarding_paths(t2.domains, entities)

    projected_alerts      = project_risk_alerts(alerts)
    projected_domains     = project_domain_entities(t2)
    projected_onboarding  = project_onboarding_paths(onboarding.paths)

    assert projected_alerts and all(e.external_id for e in projected_alerts)
    assert projected_domains and all(e.external_id for e in projected_domains)
    assert projected_onboarding and all(e.external_id for e in projected_onboarding)
    kinds = {e.entity_type for e in projected_alerts + projected_domains + projected_onboarding}
    assert "RiskAlert" in kinds
    assert "DomainEntity" in kinds
    assert "OnboardingPath" in kinds
