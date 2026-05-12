"""
ADR-0059 Pass T2 — Domain Inference (one-shot LLM call per repo).

This pass refines the SP-5 domain inference from ADR-0055 by feeding the LLM
a richer view of the codebase:

  - Classes — name + file path (for package signal)
  - Packages — top-level package tree with file counts
  - Database tables — DatabaseTable entities (from ADR-0058 when available)
  - API endpoints — ApiEndpoint URLs grouped by controller

Output is the same shape as ADR-0055 SP-5:
  - ``DomainEntity`` entities (with ``aliases`` and ``anchor_class_urns``)
  - ``REPRESENTS`` edges from each anchor class back to its DomainEntity

When ADR-0055's SP-5 has already produced DomainEntity rows for this run, T2
acts as a refinement layer: it merges aliases, expands ``anchor_class_urns``,
and emits any new edges that didn't exist. Net effect: identical contract to
SP-5 but with strictly more / better signal in the prompt.

Failure mode: any exception (LLM down, JSON malformed, etc.) returns an
empty result with a warning logged. Never raises.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

import structlog

from companybrain.llm import ChatMessage, TaskRole, get_provider
from companybrain.models.entities import (
    EDGE_REPRESENTS,
    DomainEntity,
    ExtractedEntity,
    ExtractedRelationship,
)

log = structlog.get_logger(__name__)

_CLASS_TYPES        = frozenset({"Class", "InterfaceClass", "DTO"})
_API_ENDPOINT_TYPES = frozenset({"ApiEndpoint", "Endpoint"})
_TABLE_TYPES        = frozenset({"DatabaseTable"})


@dataclass
class DomainInferencePassResult:
    domains: list[DomainEntity] = field(default_factory=list)
    edges:   list[ExtractedRelationship] = field(default_factory=list)

    @property
    def summary(self) -> dict:
        return {"domains": len(self.domains), "edges": len(self.edges)}


_DOMAIN_T2_SYSTEM_PROMPT = """\
You are reading a slice of a codebase: class names + package paths, database
tables, and API endpoints. Identify the 5–15 business / domain entities the
code represents. A domain entity is a NOUN the business cares about
(Customer, Payment, Provider, Order) — NOT a technical artifact (Controller,
Repository, Service).

Return STRICT JSON only — no prose, no markdown:

  {"domains": [
    {"name": "Payer",
     "description": "Health insurance carrier (e.g. Cigna, Aetna).",
     "aliases": ["payer", "payer_id", "PayerPlan", "BasePayer"],
     "anchor_class_qnames": ["PayerInfo", "BasePayer", "PayerPlanProvider"],
     "cross_concept_relationships": ["Plan belongs to Payer (one-to-many)"]},
    ...
  ]}

Rules:
  - Produce 5–15 domains. Drop generic infra concepts (Config, Logger).
  - "anchor_class_qnames" must list at least 3 class names from the input.
  - "aliases" include token roots that recur across many classes, DB column
    names, or API field names (e.g. "payer", "payer_id", "PayerPlan").
  - "name" should be capitalised, singular.
  - "cross_concept_relationships" lists short one-line statements about how
    this domain relates to others; empty list is fine.
"""


async def run_domain_inference_pass(
    entities: Iterable[ExtractedEntity],
    *,
    max_classes_in_prompt: int = 250,
    role: TaskRole = TaskRole.SYNTHESIS,
    max_tokens: int = 3_000,
    existing_domains: Optional[Iterable[DomainEntity]] = None,
) -> DomainInferencePassResult:
    """Run Pass T2. Returns empty result on sparse input or any error."""
    entities_list = list(entities)
    classes        = [e for e in entities_list if e.entity_type in _CLASS_TYPES]
    api_endpoints  = [e for e in entities_list if e.entity_type in _API_ENDPOINT_TYPES]
    db_tables      = [e for e in entities_list if e.entity_type in _TABLE_TYPES]

    if len(classes) < 5:
        return DomainInferencePassResult()

    sample = classes[:max_classes_in_prompt]
    user_msg = _build_user_xml(sample, api_endpoints, db_tables)

    provider = get_provider()
    try:
        raw = await provider.chat_json(
            messages=[
                ChatMessage(role="system", content=_DOMAIN_T2_SYSTEM_PROMPT),
                ChatMessage(role="user",   content=user_msg),
            ],
            role=role,
            max_tokens=max_tokens,
        )
    except Exception as exc:
        log.warning("domain_inference_pass.LLM call failed", error=str(exc))
        return DomainInferencePassResult()

    parsed = _parse(raw)
    if not parsed:
        return DomainInferencePassResult()

    by_name: dict[str, str] = {c.name: c.external_id for c in classes}
    existing_by_name: dict[str, DomainEntity] = {
        d.name: d for d in (existing_domains or [])
    }

    out = DomainInferencePassResult()
    emitted_edges: set[tuple[str, str]] = set()
    for item in parsed:
        name = _safe_name(str(item.get("name", "")).strip())
        if not name:
            continue
        anchor_qnames = [q for q in item.get("anchor_class_qnames", []) if q]
        anchor_urns = sorted({by_name[q] for q in anchor_qnames if q in by_name})
        if len(anchor_urns) < 3:
            continue
        aliases = [
            str(a) for a in (item.get("aliases") or [])
            if isinstance(a, str)
        ]
        description = str(item.get("description", "")).strip()

        prior = existing_by_name.get(name)
        if prior is not None:
            # Refinement path — merge aliases + anchors without duplicates.
            merged_aliases = _dedupe_preserve_order(prior.aliases + aliases)
            merged_anchors = sorted(set(prior.anchor_class_urns) | set(anchor_urns))
            prior.aliases = merged_aliases
            prior.anchor_class_urns = merged_anchors
            if description and not prior.description:
                prior.description = description
            domain = prior
        else:
            domain = DomainEntity(
                name=name,
                aliases=aliases,
                description=description,
                anchor_class_urns=anchor_urns,
                confidence=0.75,
            )
            out.domains.append(domain)

        for urn in anchor_urns:
            edge_key = (urn, domain.external_id)
            if edge_key in emitted_edges:
                continue
            emitted_edges.add(edge_key)
            out.edges.append(ExtractedRelationship(
                from_entity=urn, from_type="Class",
                edge_type=EDGE_REPRESENTS,
                to_entity=domain.external_id, to_type="DomainEntity",
                confidence=domain.confidence,
                evidence=f"T2 domain inference: anchor for {domain.name}",
            ))

    log.info(
        "domain_inference_pass.run complete",
        sampled_classes=len(sample),
        api_endpoints=len(api_endpoints),
        db_tables=len(db_tables),
        **out.summary,
    )
    return out


def project_domain_entities(result: DomainInferencePassResult) -> list[ExtractedEntity]:
    """Same shape as ``project_cross_file_entities`` — emits ExtractedEntity
    projections so Stage 5's writer treats DomainEntities like every other
    entity. Skipped automatically if the cross-file pass already projected
    them (the orchestrator de-dupes by ``external_id``)."""
    out: list[ExtractedEntity] = []
    for d in result.domains:
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


# ── helpers ───────────────────────────────────────────────────────────────────

def _build_user_xml(
    classes: list[ExtractedEntity],
    api_endpoints: list[ExtractedEntity],
    db_tables: list[ExtractedEntity],
) -> str:
    parts: list[str] = []

    parts.append("<classes>")
    for c in classes:
        parts.append(f'  <class name="{c.name}" file="{c.file}" />')
    parts.append("</classes>")

    # Group classes by top-level package (= first directory segment) so the
    # LLM gets a rollup view rather than 250 individual paths.
    packages: dict[str, int] = defaultdict(int)
    for c in classes:
        pkg = c.file.split("/", 1)[0] if "/" in c.file else c.file
        packages[pkg] += 1
    if packages:
        parts.append("<packages>")
        for pkg, count in sorted(packages.items(), key=lambda kv: -kv[1])[:20]:
            parts.append(f'  <package name="{pkg}" file_count="{count}" />')
        parts.append("</packages>")

    if db_tables:
        parts.append("<database_tables>")
        for t in db_tables[:50]:
            parts.append(f'  <table name="{t.name}" file="{t.file}" />')
        parts.append("</database_tables>")

    if api_endpoints:
        parts.append("<api_endpoints>")
        for ep in api_endpoints[:50]:
            parts.append(
                f'  <endpoint name="{ep.name}" signature="{ep.signature}" />'
            )
        parts.append("</api_endpoints>")

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


def _dedupe_preserve_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out
