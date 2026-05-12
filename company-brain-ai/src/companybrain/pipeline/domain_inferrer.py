"""
ADR-0055 SP-5 — Domain entity inference (one-shot LLM per repo).

After Stage 1 the brain holds a flat list of Class entities (PayerInfo,
PayerPlanProvider, BasePayer, …). Humans see "Payer / Plan / Provider"
across that list; the brain doesn't. SP-5 closes the gap with one cheap
LLM call: input is the class-name list (+ package paths and a couple of
representative DTO field lists), output is 5–15 DomainEntity objects
anchored to the classes that best represent each domain.

Outputs:
  - DomainEntity entities (with ``aliases`` and ``anchor_class_urns``)
  - REPRESENTS edges from each anchor class to its DomainEntity

The pass is intentionally one-shot to keep the cost predictable
(~$0.005/repo per ADR-0055). On parse failure the function returns an
empty result and logs a warning — never raises.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable

import structlog

from companybrain.llm import ChatMessage, TaskRole, get_provider
from companybrain.models.entities import (
    EDGE_REPRESENTS,
    DomainEntity,
    ExtractedEntity,
    ExtractedRelationship,
)

log = structlog.get_logger(__name__)


@dataclass
class DomainInferenceResult:
    domains: list[DomainEntity] = field(default_factory=list)
    edges: list[ExtractedRelationship] = field(default_factory=list)


_DOMAIN_SYSTEM_PROMPT = """\
You see a list of class names + package paths from a single codebase. Infer
the high-level business / domain concepts that these classes orbit around
("Payer", "Plan", "Provider", etc.). Group classes by concept.

Return STRICT JSON only — no prose, no markdown:

  {"domains": [
    {"name": "Payer",
     "description": "Health insurance carrier (e.g. Cigna, Aetna).",
     "aliases": ["payer_id", "PayerPlan", "BasePayer"],
     "anchor_class_qnames": ["PayerInfo", "BasePayer", "PayerPlanProvider", ...]},
    ...
  ]}

Rules:
  - Produce 5–15 domains. Drop generic infra concepts (Config, Logger).
  - "anchor_class_qnames" must list at least 3 names from the input.
  - Names should be capitalised, singular nouns.
  - Aliases include token roots that recur across many classes
    (e.g. "payer", "payer_id", "PayerPlan").
"""


async def infer_domain_entities(
    entities: Iterable[ExtractedEntity],
    *,
    max_classes_in_prompt: int = 250,
    role: TaskRole = TaskRole.SYNTHESIS,
    max_tokens: int = 2_000,
) -> DomainInferenceResult:
    """Run SP-5. Returns empty result on any error or when input is sparse."""
    classes = [
        e for e in entities
        if e.entity_type in {"Class", "InterfaceClass", "DTO"}
    ]
    if len(classes) < 5:
        return DomainInferenceResult()

    sample = classes[:max_classes_in_prompt]
    user_msg = _build_user_xml(sample)
    provider = get_provider()
    try:
        raw = await provider.chat_json(
            messages=[
                ChatMessage(role="system", content=_DOMAIN_SYSTEM_PROMPT),
                ChatMessage(role="user",   content=user_msg),
            ],
            role=role,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        log.warning("domain_inferrer.SP5 LLM call failed", error=str(exc))
        return DomainInferenceResult()

    parsed = _parse(raw)
    if not parsed:
        return DomainInferenceResult()

    out = DomainInferenceResult()
    by_name: dict[str, str] = {e.name: e.external_id for e in classes}
    for item in parsed:
        domain_name = str(item.get("name", "")).strip()
        if not domain_name:
            continue
        anchor_qnames = [q for q in item.get("anchor_class_qnames", []) if q]
        anchor_urns = sorted({by_name[q] for q in anchor_qnames if q in by_name})
        if len(anchor_urns) < 3:
            continue
        domain = DomainEntity(
            name=_safe_name(domain_name),
            aliases=[str(a) for a in (item.get("aliases") or []) if isinstance(a, str)],
            description=str(item.get("description", "")).strip(),
            anchor_class_urns=anchor_urns,
            confidence=0.7,
        )
        out.domains.append(domain)
        for urn in anchor_urns:
            out.edges.append(ExtractedRelationship(
                from_entity=urn, from_type="Class",
                edge_type=EDGE_REPRESENTS,
                to_entity=domain.external_id, to_type="DomainEntity",
                confidence=domain.confidence,
                evidence=f"domain inference: anchor for {domain.name}",
            ))

    log.info(
        "domain_inferrer.infer_domain_entities",
        sampled_classes=len(sample),
        domains=len(out.domains),
        edges=len(out.edges),
    )
    return out


# ── helpers ───────────────────────────────────────────────────────────────────

def _build_user_xml(classes: list[ExtractedEntity]) -> str:
    parts = ["<classes>"]
    for c in classes:
        parts.append(f'  <class name="{c.name}" file="{c.file}" />')
    parts.append("</classes>")
    return "\n".join(parts)


def _parse(raw: str) -> list[dict]:
    if not raw:
        return []
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return []
    items = obj.get("domains") if isinstance(obj, dict) else None
    return [it for it in (items or []) if isinstance(it, dict)]


def _safe_name(raw: str) -> str:
    out: list[str] = []
    for ch in raw:
        if ch.isalnum() or ch in "_-":
            out.append(ch)
        else:
            out.append("_")
    return "".join(out).strip("_") or "domain"
