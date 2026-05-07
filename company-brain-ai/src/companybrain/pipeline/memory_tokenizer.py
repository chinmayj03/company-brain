"""
MemoryTokenizer — Pipeline Stage 3.5 (ADR-004: Tiered Memory).

Runs AFTER context synthesis (Stage 3) and BEFORE gap detection (Stage 4).

For every synthesised BusinessContext, generates two compact memory tokens:

  T0 (~15 tokens): one-liner for "I've heard of this" awareness
      "{name} ({entity_type}) — {change_risk} risk — {file}"

  T1 (~100 tokens): summary for "I know what this does" scanning
      "{purpose}
       Risk: {change_risk} — {change_risk_reason}
       Invariants: {first two invariants}
       Owner: {owner_team}"

These tokens are:
  1. Stored in entity metadata ("t0", "t1") → sent to Java via pipeline payload
  2. Used by ContextAssemblerService for T0/T1 block rendering (no extra LLM call)
  3. Used as a fast pre-filter in Ask: T0 scan identifies candidate nodes,
     T1 decides whether to load full T2, keeping prompt assembly sub-100ms

No LLM is required — tokens are generated deterministically from BusinessContext.
"""
from __future__ import annotations

from dataclasses import dataclass

import structlog

from companybrain.models.entities import ExtractedEntity, BusinessContext

log = structlog.get_logger(__name__)


@dataclass
class MemoryToken:
    entity_external_id: str
    t0: str   # ~15 tokens — name + type + risk + file
    t1: str   # ~100 tokens — purpose + risk reason + invariants + owner


class MemoryTokenizer:
    """
    Stage 3.5: generate T0/T1 memory tokens from synthesised BusinessContext objects.

    Usage:
        tokens = MemoryTokenizer().tokenize_all(entities, contexts)
        # tokens: dict[external_id → MemoryToken]
    """

    def tokenize_all(
        self,
        entities: list[ExtractedEntity],
        contexts: dict[str, BusinessContext],
    ) -> dict[str, MemoryToken]:
        """
        Generate T0/T1 tokens for every entity that has a synthesised context.
        Entities without a BusinessContext still get a T0 from structural metadata.
        """
        results: dict[str, MemoryToken] = {}

        for entity in entities:
            ctx = contexts.get(entity.external_id)
            token = self._tokenize_one(entity, ctx)
            results[entity.external_id] = token

        log.info("[memory] Tokenized %d entities (T0+T1)", len(results))
        return results

    def _tokenize_one(
        self,
        entity: ExtractedEntity,
        ctx: BusinessContext | None,
    ) -> MemoryToken:
        # ── T0: one-liner ──────────────────────────────────────────────────────
        change_risk = (ctx.change_risk if ctx else "UNKNOWN").upper()
        short_file  = entity.file.split("/")[-1] if entity.file else ""
        t0 = f"{entity.name} ({entity.entity_type}) — {change_risk} risk — {short_file}"

        # ── T1: ~100-token summary ─────────────────────────────────────────────
        if ctx:
            purpose = _truncate(ctx.purpose, 200)

            risk_line = f"Risk: {change_risk}"
            if ctx.change_risk_reason:
                risk_line += f" — {_truncate(ctx.change_risk_reason, 100)}"

            invariant_lines = ""
            if ctx.invariants:
                shown = ctx.invariants[:2]
                invariant_lines = "\nInvariants: " + "; ".join(shown)
                if len(ctx.invariants) > 2:
                    invariant_lines += f" (+{len(ctx.invariants) - 2} more)"

            owner_line = f"\nOwner: {ctx.owner_team}" if ctx.owner_team else ""

            t1 = f"{purpose}\n{risk_line}{invariant_lines}{owner_line}"
        else:
            # No synthesis yet — build from structural facts only
            sig = f" — `{_truncate(entity.signature, 80)}`" if entity.signature else ""
            t1 = f"{entity.entity_type} `{entity.name}`{sig}\nFile: {entity.file}\n(No business context synthesised yet — run pipeline to enrich)"

        return MemoryToken(
            entity_external_id=entity.external_id,
            t0=t0.strip(),
            t1=t1.strip(),
        )


def memory_tokens_to_metadata(tokens: dict[str, MemoryToken]) -> dict[str, dict]:
    """
    Convert the token dict to a form suitable for merging into entity metadata
    and including in the pipeline payload to Java.

    Returns: { external_id: {"t0": "...", "t1": "..."} }
    """
    return {
        ext_id: {"t0": tok.t0, "t1": tok.t1}
        for ext_id, tok in tokens.items()
    }


def _truncate(s: str | None, max_chars: int) -> str:
    if not s:
        return ""
    s = s.strip()
    if len(s) <= max_chars:
        return s
    return s[:max_chars].rstrip() + "…"
