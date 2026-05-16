"""
Structured response schema for POST /query — ADR-0043 WS3.

Every factual claim in summary/call_chain must end with a URN citation so
the frontend can wire click-to-jump. The LLM is instructed to drop claims
it cannot cite rather than fabricate node names.
"""
from __future__ import annotations

from typing import Annotated, Any, Literal, Optional

from pydantic import BaseModel, BeforeValidator, computed_field


def _coerce_confidence(v: Any) -> float:
    """Normalize confidence to float regardless of input format.

    Handles both the numeric form (0.85) and the legacy ADR-0005 object form
    {"value": 0.85, "rationale": "..."} so JSON from mixed-vintage brain stores
    doesn't fail Pydantic validation.
    """
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        raw = v.get("value") or v.get("score") or v.get("confidence")
        if raw is not None:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.9


ConfidenceFloat = Annotated[float, BeforeValidator(_coerce_confidence)]


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
    confidence: ConfidenceFloat = 0.9


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

    # ── ADR-0061 E1: exploration telemetry ───────────────────────────────
    # Set by query.py when ExplorationAgent fires. Clients can inspect:
    #   exploration_agent_invoked: bool
    #   exploration_rounds: int
    #   exploration_tool_calls: int
    telemetry: dict = {}

    # ── Computed / serialised convenience fields ──────────────────────────────

    @computed_field  # type: ignore[misc]
    @property
    def cited_entity_urns(self) -> list[str]:
        """Deduplicated list of URNs cited in affected_entities + call_chain.

        Combines both sources so E2E checks and frontend consumers get every
        URN the response references without having to merge two lists.
        """
        seen: set[str] = set()
        out: list[str] = []
        for c in self.affected_entities:
            if c.urn and c.urn not in seen:
                seen.add(c.urn)
                out.append(c.urn)
        for step in self.call_chain:
            if step.urn and step.urn not in seen:
                seen.add(step.urn)
                out.append(step.urn)
        return out

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
