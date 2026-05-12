"""
Structured response schema for POST /query — ADR-0043 WS3.

Every factual claim in summary/call_chain must end with a URN citation so
the frontend can wire click-to-jump. The LLM is instructed to drop claims
it cannot cite rather than fabricate node names.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class CallChainStep(BaseModel):
    ord: int
    urn: str
    name: str
    role: Literal[
        "entry", "controller", "service", "repository",
        "query", "external", "frontend", "test", "other"
    ]
    edge_in: Optional[str] = None
    annotations: list[str] = []
    one_liner: str


class SqlBlock(BaseModel):
    source_urn: str
    language: Literal["sql", "jpql", "jooq", "cypher", "mongo", "other"]
    body: str


class Citation(BaseModel):
    urn: str
    name: str
    why_relevant: str
    confidence: float


class RiskAssessment(BaseModel):
    level: Literal["low", "medium", "high"]
    reason: str
    blast_radius_count: int
    sample_affected: list[Citation] = []


class Confidence(BaseModel):
    level: Literal["high", "medium", "low"]
    rationale: str


class QueryResponse(BaseModel):
    """
    Typed response returned by POST /query (ADR-0043 WS3).

    ``summary`` is backward-compatible with the old ``answer: str`` field —
    UI consumers that only read summary continue to work on day 1.
    """
    summary: str
    # ADR-0049 O5a-5: raw markdown field — avoids double-encoding markdown
    # inside a JSON string (every backtick and newline was escaped, costing 2×
    # tokens and forcing the client to decode twice).  summary stays for one
    # release as a deprecated alias.
    summary_md: Optional[str] = None
    call_chain: list[CallChainStep] = []
    sql_quotes: list[SqlBlock] = []
    affected_entities: list[Citation] = []
    change_risk: Optional[RiskAssessment] = None
    confidence: Confidence
    caveats: list[str] = []
    follow_up_questions: list[str] = []
    raw_markdown: str = ""
    # ADR-0052 P6: per-entity sticky notes attached to any URN cited in
    # affected_entities / call_chain. Each item: {urn, note, author?, created_at?}.
    # Empty when no notes exist or the notes table is unavailable.
    notes: list[dict] = []

    # ── ADR-0059 additions ────────────────────────────────────────────────
    # Surfaced from .brain/ when the pipeline produced them. Each is a list
    # of plain dicts so the contract is stable across pydantic versions and
    # we don't import internal pipeline dataclasses into the API model.
    # risk_alerts: [{kind, severity, affected_entity_urn, message}, ...]
    # domain_entities: [{name, aliases, description, anchor_class_urns}, ...]
    # onboarding_paths: [{domain_name, anchor_class_urns, rationale}, ...]
    risk_alerts: list[dict] = []
    domain_entities: list[dict] = []
    onboarding_paths: list[dict] = []

    # ── ADR-0061 additions ────────────────────────────────────────────────
    # Free-form telemetry the query path attaches so the UI / acceptance tests
    # can assert which iterative-exploration features fired. Stable keys:
    #   exploration_agent_invoked: bool       (E1)
    #   exploration_agent_steps:   int        (E1)
    #   reread_invoked:            bool       (E2)
    #   clarification_returned:    bool       (E5)
    #   cross_repo_hits:           int        (E6)
    telemetry: dict = {}
    # ADR-0061 E5: when set, the LLM was bypassed and the client must render a
    # disambiguation prompt instead of an answer. ``interpretations`` is a list
    # of {id, description}; ``suggested_followup`` is a one-line example query
    # string the UI can drop into the search bar.
    ambiguity: bool = False
    interpretations: list[dict] = []
    suggested_followup: Optional[str] = None
    # ADR-0061 E6: per-result Pattern→Pattern matches surfaced across the
    # caller's other workspaces. Each item:
    #   {source_urn, source_name, target_workspace, target_urn, target_name,
    #    score, note}
    cross_repo_insights: list[dict] = []

    # Legacy aliases so callers that read .answer or .sources keep working.
    @property
    def answer(self) -> str:
        return self.summary

    @property
    def sources(self) -> list[dict]:
        return [{"urn": c.urn, "name": c.name} for c in self.affected_entities]

    @property
    def affected_nodes(self) -> list[dict]:
        return [{"urn": c.urn, "name": c.name} for c in self.affected_entities]
