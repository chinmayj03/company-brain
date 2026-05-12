"""
ADR-0055 SP-1 — Idiom & pattern detection (deterministic).

Scans the relationship graph after Stage 2 and emits Pattern entities for
repeating call shapes that humans treat as idioms (e.g. "always call
sanitiseRequest before persist", "soft-delete via is_current=true filter").

The detector is intentionally cheap: it only looks at edge counts and a
small amount of evidence-string normalisation. The output is a list of
Pattern objects plus IMPLEMENTS_PATTERN edges that connect each
participating entity back to the pattern.

Heuristics (kept narrow on purpose; SP-3 covers the LLM-rich cases):
  - Shared callee:    a callee invoked from >= N distinct callers, with
                       similar argument shapes, becomes a Pattern.
  - Shared filter:    a column read with the same evidence literal across
                       >= N callers becomes a Pattern (e.g. is_current=true).
  - Wrap idiom:       a try/catch translation wrapper repeated across >= N
                       callers becomes a Pattern (CATCHES + WRAPS_EXCEPTION
                       targeting the same exception type).

Tunables come from companybrain.config.settings (see config additions made
by ADR-0055): ``cross_file_pattern_min_instances`` defaults to 5.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

import structlog

from companybrain.models.entities import (
    EDGE_IMPLEMENTS_PATTERN,
    ExtractedRelationship,
    Pattern,
)

log = structlog.get_logger(__name__)


@dataclass
class IdiomDetectionResult:
    patterns: list[Pattern]
    edges: list[ExtractedRelationship]


def detect_idioms(
    relationships: Iterable[ExtractedRelationship],
    *,
    min_instances: int = 5,
) -> IdiomDetectionResult:
    """Return Pattern entities + IMPLEMENTS_PATTERN edges discovered in the graph.

    The function is pure: same input → same output, no I/O, no LLM. Caller
    is responsible for deduping with existing relationships before write.
    """
    rels = list(relationships)
    if not rels:
        return IdiomDetectionResult(patterns=[], edges=[])

    patterns: list[Pattern] = []
    edges: list[ExtractedRelationship] = []

    patterns_by_id: dict[str, Pattern] = {}

    def _emit(pattern: Pattern, callers: list[str]) -> None:
        patterns_by_id[pattern.external_id] = pattern
        for caller in callers:
            edges.append(_pattern_edge(caller, pattern))

    # ── (a) shared-callee idiom ────────────────────────────────────────────
    for callee, callers in _group_callers_by_callee(rels).items():
        unique_callers = sorted({c for c in callers if c and c != callee})
        if len(unique_callers) < min_instances:
            continue
        pattern = Pattern(
            name=_safe_name("shared_call_" + _short_local_name(callee)),
            description=(
                f"{len(unique_callers)} callers all invoke {callee}. "
                "Repeated call shape suggests a project-wide idiom."
            ),
            instance_count=len(unique_callers),
            confidence=_confidence_from_count(len(unique_callers), min_instances),
            inferred_from="deterministic",
            instance_urns=unique_callers,
        )
        _emit(pattern, unique_callers)

    # ── (b) shared-filter idiom ────────────────────────────────────────────
    for (column, literal), callers in _group_filter_callers(rels).items():
        unique_callers = sorted({c for c in callers if c})
        if len(unique_callers) < min_instances:
            continue
        pattern = Pattern(
            name=_safe_name(f"shared_filter_{_short_local_name(column)}_{literal}"),
            description=(
                f"{len(unique_callers)} callers filter {column}={literal} on every "
                "read. Likely a soft-delete / tenancy convention."
            ),
            instance_count=len(unique_callers),
            confidence=_confidence_from_count(len(unique_callers), min_instances),
            inferred_from="deterministic",
            instance_urns=unique_callers,
        )
        _emit(pattern, unique_callers)

    # ── (c) exception-translation idiom ────────────────────────────────────
    for exception_type, callers in _group_wrap_callers(rels).items():
        unique_callers = sorted({c for c in callers if c})
        if len(unique_callers) < min_instances:
            continue
        pattern = Pattern(
            name=_safe_name(f"wrap_{_short_local_name(exception_type)}"),
            description=(
                f"{len(unique_callers)} sites wrap and rethrow {exception_type}. "
                "Convention encodes the project's exception-translation rule."
            ),
            instance_count=len(unique_callers),
            confidence=_confidence_from_count(len(unique_callers), min_instances),
            inferred_from="deterministic",
            instance_urns=unique_callers,
        )
        _emit(pattern, unique_callers)

    log.info(
        "idiom_detector.detect_idioms",
        relationships=len(rels),
        patterns=len(patterns_by_id),
        edges=len(edges),
        min_instances=min_instances,
    )
    return IdiomDetectionResult(
        patterns=list(patterns_by_id.values()), edges=edges,
    )


# ── grouping helpers ──────────────────────────────────────────────────────────

def _group_callers_by_callee(
    rels: list[ExtractedRelationship],
) -> dict[str, list[str]]:
    """Group caller external_ids by callee for behaviour edges."""
    out: dict[str, list[str]] = defaultdict(list)
    for r in rels:
        if r.edge_type not in {"CALLS", "INVOKES", "USES", "DELEGATES_TO"}:
            continue
        out[r.to_entity].append(r.from_entity)
    return out


def _group_filter_callers(
    rels: list[ExtractedRelationship],
) -> dict[tuple[str, str], list[str]]:
    """Group caller external_ids by (column, literal) for filter idioms.

    The literal is parsed from the evidence string with a tiny normaliser; if
    no literal can be extracted the caller still contributes under literal="*".
    """
    out: dict[tuple[str, str], list[str]] = defaultdict(list)
    for r in rels:
        if r.edge_type != "READS_COLUMN":
            continue
        literal = _extract_filter_literal(r.evidence, r.to_entity)
        out[(r.to_entity, literal)].append(r.from_entity)
    return out


def _group_wrap_callers(
    rels: list[ExtractedRelationship],
) -> dict[str, list[str]]:
    """Group caller external_ids by the wrapped exception type."""
    out: dict[str, list[str]] = defaultdict(list)
    for r in rels:
        if r.edge_type not in {"WRAPS_EXCEPTION", "CATCHES"}:
            continue
        out[r.to_entity].append(r.from_entity)
    return out


def _extract_filter_literal(evidence: str, column: str) -> str:
    """Pull the literal value from a where-clause-style evidence string.

    Cheap and forgiving: looks for ``column=value`` or ``column = value`` in
    the evidence. Falls back to the wildcard "*" when nothing matches.
    """
    if not evidence or not column:
        return "*"
    needle = column.split(".")[-1].lower()
    text = evidence.lower()
    idx = text.find(needle)
    if idx < 0:
        return "*"
    tail = text[idx + len(needle):].lstrip()
    if not tail.startswith("="):
        return "*"
    tail = tail[1:].lstrip()
    end = 0
    for ch in tail:
        if ch.isalnum() or ch in "_'\"":
            end += 1
        else:
            break
    if end == 0:
        return "*"
    literal = tail[:end].strip("'\"")
    return literal or "*"


# ── name + confidence helpers ─────────────────────────────────────────────────

def _short_local_name(external_id: str) -> str:
    """Return the trailing local part of a fully-qualified URN/external_id."""
    if not external_id:
        return "unknown"
    for sep in ("::", ".", "/"):
        if sep in external_id:
            external_id = external_id.split(sep)[-1]
    return external_id or "unknown"


def _safe_name(raw: str) -> str:
    """Turn a name into a stable, filesystem-safe identifier."""
    out = []
    for ch in raw:
        if ch.isalnum() or ch in "_-":
            out.append(ch)
        else:
            out.append("_")
    name = "".join(out).strip("_")
    return name or "pattern"


def _confidence_from_count(count: int, threshold: int) -> float:
    """Map a population count → confidence in [0.6, 0.95]."""
    if count <= threshold:
        return 0.6
    over = count - threshold
    return min(0.95, 0.6 + 0.05 * over)


def _pattern_edge(caller: str, pattern: Pattern) -> ExtractedRelationship:
    return ExtractedRelationship(
        from_entity=caller,
        from_type="Function",
        edge_type=EDGE_IMPLEMENTS_PATTERN,
        to_entity=pattern.external_id,
        to_type="Pattern",
        confidence=pattern.confidence,
        evidence=f"deterministic-idiom: {pattern.name}",
    )
