"""
ADR-0055 SP-3 + SP-4 — Cross-method invariant + implicit-contract inference.

SP-3 builds windows of related methods (5–10 per window, default same-class
or same-call-chain) and asks a single batched LLM call to identify
invariants that hold ACROSS the whole window — e.g. "all 5 reads filter
is_current=true". SharedInvariant entities + SHARES_INVARIANT edges are
emitted.

SP-4 batches caller pre-conditions and callee post-conditions for a method
and its callers, returning ImplicitContract objects that the orchestrator
attaches to the method's BusinessContext.

Both passes degrade gracefully:
  - LLM unavailable / parse failure → empty result, no exceptions raised.
  - Empty input → empty result, no LLM call made.

Window construction is deterministic + cheap (group by class via
external_id prefix; cap window size). Per ADR-0055 §"Effort estimate" the
LLM cost is ~$0.005/repo because most windows are short.
"""
from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Optional

import structlog

from companybrain.llm import ChatMessage, TaskRole, get_provider
from companybrain.models.entities import (
    EDGE_HAS_IMPLICIT_CONTRACT,
    EDGE_SHARES_INVARIANT,
    ExtractedEntity,
    ExtractedRelationship,
    ImplicitContract,
    SharedInvariant,
)

log = structlog.get_logger(__name__)


@dataclass
class InvariantInferenceResult:
    invariants: list[SharedInvariant] = field(default_factory=list)
    contracts:  list[ImplicitContract] = field(default_factory=list)
    edges:      list[ExtractedRelationship] = field(default_factory=list)
    cost_usd:   float = 0.0


# ── SP-3: invariants over method windows ──────────────────────────────────────

_INVARIANT_SYSTEM_PROMPT = """\
You analyse small windows of related methods and identify invariants that
hold ACROSS the whole window, not within one method. Return STRICT JSON
with no prose, no markdown.

Schema:
  {"invariants": [
     {"name": "soft_delete_filter",
      "statement": "all reads filter is_current=true",
      "affected_qnames": ["ClassA.method1", "ClassA.method2", ...]},
     ...
  ]}

Rules:
  - Skip invariants that are obvious from a single method body.
  - "affected_qnames" must reference qnames present in the input window.
  - Produce 0-5 invariants per window. Prefer high-signal facts.
  - Return {"invariants": []} if nothing crosses the window.
"""


async def infer_shared_invariants(
    entities: Iterable[ExtractedEntity],
    *,
    window_size: int = 8,
    min_window: int = 3,
    role: TaskRole = TaskRole.BALANCED,
    max_tokens: int = 1_500,
) -> InvariantInferenceResult:
    """Run SP-3 across all class-grouped windows."""
    out = InvariantInferenceResult()
    windows = _build_class_windows(entities, window_size=window_size, min_window=min_window)
    if not windows:
        return out

    provider = get_provider()
    for window in windows:
        try:
            raw = await provider.chat_json(
                messages=[
                    ChatMessage(role="system", content=_INVARIANT_SYSTEM_PROMPT),
                    ChatMessage(role="user",   content=_build_invariant_user_xml(window)),
                ],
                role=role,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            log.warning(
                "invariant_inferrer.SP3 LLM call failed",
                error=str(exc), window_size=len(window),
            )
            continue

        parsed = _parse_invariants(raw)
        for item in parsed:
            qnames = [q for q in item.get("affected_qnames", []) if q]
            affected_urns = _qnames_to_external_ids(qnames, window)
            if len(affected_urns) < min_window:
                continue
            inv = SharedInvariant(
                name=_safe_name(item.get("name") or "shared_invariant"),
                statement=str(item.get("statement", "")).strip(),
                affected_method_urns=affected_urns,
                evidence_method_urns=affected_urns,
                confidence=0.7,
            )
            out.invariants.append(inv)
            for urn in affected_urns:
                out.edges.append(ExtractedRelationship(
                    from_entity=urn, from_type="Function",
                    edge_type=EDGE_SHARES_INVARIANT,
                    to_entity=inv.external_id, to_type="SharedInvariant",
                    confidence=inv.confidence,
                    evidence=f"window-inferred invariant: {inv.statement[:120]}",
                ))

    log.info(
        "invariant_inferrer.infer_shared_invariants",
        windows=len(windows),
        invariants=len(out.invariants),
        edges=len(out.edges),
    )
    return out


# ── SP-4: implicit contracts per method+callers ───────────────────────────────

_CONTRACT_SYSTEM_PROMPT = """\
You read one method and the call sites that invoke it, and infer:
  preconditions  — facts the callers seem to assume hold before the call,
  postconditions — facts the method seems to guarantee after returning.

Return STRICT JSON only:
  {"preconditions": ["..."], "postconditions": ["..."]}

Rules:
  - Cite only what is supported by the bodies given.
  - Skip restatements of the signature.
  - Empty list is OK when there is nothing concrete to add.
"""


async def infer_implicit_contracts(
    entities: Iterable[ExtractedEntity],
    relationships: Iterable[ExtractedRelationship],
    *,
    min_callers: int = 2,
    role: TaskRole = TaskRole.BALANCED,
    max_tokens: int = 800,
) -> InvariantInferenceResult:
    """Run SP-4 across all methods that have at least ``min_callers`` callers."""
    out = InvariantInferenceResult()
    by_id = {e.external_id: e for e in entities}
    callers_by_callee = _callers_by_callee(relationships)

    candidates = [
        (callee, callers)
        for callee, callers in callers_by_callee.items()
        if len(callers) >= min_callers and callee in by_id
    ]
    if not candidates:
        return out

    provider = get_provider()
    for callee_id, caller_ids in candidates:
        callee = by_id[callee_id]
        callers = [by_id[c] for c in caller_ids if c in by_id]
        if not callers:
            continue
        try:
            raw = await provider.chat_json(
                messages=[
                    ChatMessage(role="system", content=_CONTRACT_SYSTEM_PROMPT),
                    ChatMessage(role="user",   content=_build_contract_user_xml(callee, callers)),
                ],
                role=role,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            log.warning(
                "invariant_inferrer.SP4 LLM call failed",
                error=str(exc), callee=callee_id,
            )
            continue

        parsed = _parse_contract(raw)
        if not parsed:
            continue
        contract = ImplicitContract(
            method_external_id=callee_id,
            preconditions=[s for s in parsed.get("preconditions", []) if s],
            postconditions=[s for s in parsed.get("postconditions", []) if s],
            confidence=0.65,
        )
        if not (contract.preconditions or contract.postconditions):
            continue
        out.contracts.append(contract)
        # Surface as an edge so it shows up in the graph view too.
        out.edges.append(ExtractedRelationship(
            from_entity=callee_id, from_type=callee.entity_type,
            edge_type=EDGE_HAS_IMPLICIT_CONTRACT,
            to_entity=callee_id, to_type=callee.entity_type,
            confidence=contract.confidence,
            evidence="implicit contract inferred from caller pattern",
        ))

    log.info(
        "invariant_inferrer.infer_implicit_contracts",
        candidates=len(candidates),
        contracts=len(out.contracts),
    )
    return out


# ── helpers ───────────────────────────────────────────────────────────────────

def _build_class_windows(
    entities: Iterable[ExtractedEntity], *, window_size: int, min_window: int,
) -> list[list[ExtractedEntity]]:
    """Group methods by class and split into windows of up to ``window_size``."""
    by_class: dict[str, list[ExtractedEntity]] = defaultdict(list)
    for e in entities:
        if e.entity_type not in {"Function", "Method", "InterfaceMethod"}:
            continue
        class_key = _class_key_for(e)
        by_class[class_key].append(e)

    windows: list[list[ExtractedEntity]] = []
    for class_key, group in by_class.items():
        if len(group) < min_window:
            continue
        for i in range(0, len(group), window_size):
            chunk = group[i:i + window_size]
            if len(chunk) >= min_window:
                windows.append(chunk)
    return windows


def _class_key_for(entity: ExtractedEntity) -> str:
    """Best-effort owning class id for a method entity."""
    name = entity.name or ""
    if "." in name:
        return f"{entity.repo}/{entity.file}::{name.rsplit('.', 1)[0]}"
    return f"{entity.repo}/{entity.file}"


def _build_invariant_user_xml(window: list[ExtractedEntity]) -> str:
    parts = ["<window>"]
    for e in window:
        body = (e.code_snippet or e.signature or "").strip()
        parts.append(f'  <method qname="{e.name}">')
        parts.append(body)
        parts.append("  </method>")
    parts.append("</window>")
    return "\n".join(parts)


def _build_contract_user_xml(
    callee: ExtractedEntity, callers: list[ExtractedEntity],
) -> str:
    parts = [
        f'<callee qname="{callee.name}">',
        (callee.code_snippet or callee.signature or "").strip(),
        "</callee>",
        "<callers>",
    ]
    for c in callers[:6]:
        parts.append(f'  <caller qname="{c.name}">')
        parts.append((c.code_snippet or c.signature or "").strip())
        parts.append("  </caller>")
    parts.append("</callers>")
    return "\n".join(parts)


def _parse_invariants(raw: str) -> list[dict]:
    if not raw:
        return []
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return []
    items = obj.get("invariants") if isinstance(obj, dict) else None
    return [it for it in (items or []) if isinstance(it, dict)]


def _parse_contract(raw: str) -> Optional[dict]:
    if not raw:
        return None
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    pre  = obj.get("preconditions")  or []
    post = obj.get("postconditions") or []
    return {
        "preconditions":  [str(p).strip() for p in pre  if isinstance(p, str)],
        "postconditions": [str(p).strip() for p in post if isinstance(p, str)],
    }


def _qnames_to_external_ids(
    qnames: list[str], window: list[ExtractedEntity],
) -> list[str]:
    by_name: dict[str, str] = {}
    for e in window:
        by_name[e.name] = e.external_id
        # Also accept a trailing-method-name match for callers that drop the class prefix.
        local = e.name.split(".")[-1] if "." in e.name else e.name
        by_name.setdefault(local, e.external_id)
    out: list[str] = []
    for q in qnames:
        if q in by_name:
            out.append(by_name[q])
    return sorted(set(out))


def _callers_by_callee(
    rels: Iterable[ExtractedRelationship],
) -> dict[str, list[str]]:
    out: dict[str, list[str]] = defaultdict(list)
    for r in rels:
        if r.edge_type not in {"CALLS", "INVOKES", "DELEGATES_TO"}:
            continue
        out[r.to_entity].append(r.from_entity)
    return out


def _safe_name(raw: str) -> str:
    out: list[str] = []
    for ch in raw:
        if ch.isalnum() or ch in "_-":
            out.append(ch)
        else:
            out.append("_")
    return "".join(out).strip("_") or "shared_invariant"
