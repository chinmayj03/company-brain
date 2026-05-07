"""
Hierarchical context architecture for LLM extraction — inspired by CPU memory hierarchy.

Three tiers:

  L1 — Node-Local Context (always in prompt, per-extraction-call)
       Current code unit + direct call chain + last 3 git commits + annotations.
       Already handled by CodeTracer / FocalContext.  Not modelled here.

  L2 — Pipeline Shared Context  ← this module
       Accumulates as nodes are extracted within a pipeline run.
       5th extraction knows what the 1st found.
       In-memory per run; serialised to Redis on job completion.

  Main Memory — Graph Store (Postgres / Redis)
       Full persistent knowledge from all prior runs.
       Paged in selectively (only for high-uncertainty nodes).
       Fetched via Java backend APIs.

See also:
  shared_context_accumulator.py  — rule-based updater for L2 after each extraction
  context_manager_agent.py       — LLM-based agent that assembles the optimal prompt
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class L2SharedContext:
    """
    Pipeline-scoped shared context — the 'L2 cache' of the extraction layer.

    Grows as code units are processed within one run_pipeline() invocation.
    Injected as a 'Workspace Context' section into every subsequent LLM extraction call.
    """

    # ── What L2 holds ──────────────────────────────────────────────────────────

    # Domain abbreviation → plain-English expansion
    # e.g. {"NIQ": "Network IQ — competitiveness scoring service for payers"}
    domain_glossary: dict[str, str] = field(default_factory=dict)

    # Class name → {"role": "service|repository|client|controller", "file": "..."}
    # Lets later calls understand the role of any class referenced in the call chain
    service_registry: dict[str, dict] = field(default_factory=dict)

    # Detected architecture patterns — appended by SharedContextAccumulator
    # e.g. ["SAGA/event pattern in PaymentService", "Reactive (Mono/Flux) in OrderService"]
    pattern_library: list[str] = field(default_factory=list)

    # Cross-cutting concerns: auth, caching, auditing, async
    # e.g. ["Spring Security @PreAuthorize on CompetitivenessController"]
    cross_cutting: list[str] = field(default_factory=list)

    # Field name → semantic description (for SchemaField / DatabaseColumn entities)
    # e.g. {"niq_score": "0-100 competitiveness rank for a payer network"}
    field_semantics: dict[str, str] = field(default_factory=dict)

    # Top-confidence entities discovered so far (capped at 30, sorted by confidence desc)
    # Injected for role=model/dto so the LLM can see what types are already known
    entity_catalog: list[dict] = field(default_factory=list)

    # ── Query interface ────────────────────────────────────────────────────────

    def is_empty(self) -> bool:
        return not any([
            self.domain_glossary,
            self.service_registry,
            self.pattern_library,
            self.cross_cutting,
            self.field_semantics,
            self.entity_catalog,
        ])

    def to_prompt_section(
        self,
        node_role: str = "",
        budget_chars: int = 2000,
        focus_keys: Optional[list[str]] = None,
    ) -> str:
        """
        Render a compact 'Workspace Context' section for injection into an extraction prompt.

        Args:
            node_role:   Role of the code unit being extracted (service, model, controller…)
                         Used to prioritise which L2 categories matter most.
            budget_chars: Hard character limit on the returned section.
            focus_keys:  Optional list of L2 category names to include
                         (as returned by the CM Agent). If None, uses role-based heuristics.

        Returns:
            A markdown section string, or empty string if L2 is empty / nothing relevant.
        """
        if self.is_empty():
            return ""

        sections: list[str] = []

        want = set(focus_keys) if focus_keys else None

        # ── Domain glossary (always relevant — explains abbreviations) ──────────
        if self.domain_glossary and (want is None or "domain_glossary" in want):
            items = list(self.domain_glossary.items())[:10]
            gloss = "; ".join(f"{k}={v[:50]}" for k, v in items)
            sections.append(f"Domain vocabulary: {gloss}")

        # ── Service registry (most useful for service/controller/repository roles) ─
        if self.service_registry and (want is None or "service_registry" in want):
            if want is not None or node_role in ("service", "controller", "handler", "repository", "client"):
                reg_items = list(self.service_registry.items())[:10]
                reg = "; ".join(f"{k}({v['role']})" for k, v in reg_items)
                sections.append(f"Known services: {reg}")

        # ── Architecture patterns ─────────────────────────────────────────────
        if self.pattern_library and (want is None or "pattern_library" in want):
            sections.append(f"Architecture patterns: {'; '.join(self.pattern_library[:3])}")

        # ── Cross-cutting concerns ────────────────────────────────────────────
        if self.cross_cutting and (want is None or "cross_cutting" in want):
            sections.append(f"Cross-cutting concerns: {'; '.join(self.cross_cutting[:3])}")

        # ── Field semantics (most useful for model/DTO/schema roles) ──────────
        if self.field_semantics and (want is None or "field_semantics" in want):
            if want is not None or node_role in ("model", "dto", "entity", "schema", "domain"):
                fsem = "; ".join(f"{k}={v[:50]}" for k, v in list(self.field_semantics.items())[:8])
                sections.append(f"Field semantics: {fsem}")

        # ── Entity catalog (high-confidence already-extracted entities) ────────
        if self.entity_catalog and (want is None or "entity_catalog" in want):
            high_conf = [e for e in self.entity_catalog if e.get("confidence", 0) >= 0.85][:5]
            if high_conf:
                cat = ", ".join(f"{e['name']}({e['entity_type']})" for e in high_conf)
                sections.append(f"Known entities (high-confidence): {cat}")

        if not sections:
            return ""

        result = (
            "### Workspace Context (accumulated from prior extractions in this run)\n"
            + "\n".join(f"- {s}" for s in sections)
        )

        # Hard budget — truncate gracefully
        if len(result) > budget_chars:
            result = result[:budget_chars].rsplit("\n", 1)[0] + "\n- ... (truncated)"

        return result

    def compact_summary(self) -> str:
        """
        Very short summary for the CM Agent's input (not the full section).
        ~200 chars max.
        """
        parts = []
        if self.domain_glossary:
            parts.append(f"glossary[{len(self.domain_glossary)}]={','.join(list(self.domain_glossary.keys())[:5])}")
        if self.service_registry:
            parts.append(f"services[{len(self.service_registry)}]={','.join(list(self.service_registry.keys())[:5])}")
        if self.pattern_library:
            parts.append(f"patterns={self.pattern_library[0][:40]}")
        if self.cross_cutting:
            parts.append(f"cross-cutting={self.cross_cutting[0][:40]}")
        if self.field_semantics:
            parts.append(f"field_semantics[{len(self.field_semantics)}]")
        if self.entity_catalog:
            parts.append(f"entities[{len(self.entity_catalog)}]")
        return " | ".join(parts) if parts else "(empty)"

    def snapshot(self) -> dict:
        """Serialisable snapshot for Redis persistence / debugging."""
        return {
            "domain_glossary":  self.domain_glossary,
            "service_registry": self.service_registry,
            "pattern_library":  self.pattern_library,
            "cross_cutting":    self.cross_cutting,
            "field_semantics":  self.field_semantics,
            "entity_catalog":   self.entity_catalog[:20],
        }

    @classmethod
    def from_snapshot(cls, data: dict) -> "L2SharedContext":
        """Rehydrate from a Redis snapshot (e.g. for follow-up pipeline runs)."""
        return cls(
            domain_glossary  = data.get("domain_glossary", {}),
            service_registry = data.get("service_registry", {}),
            pattern_library  = data.get("pattern_library", []),
            cross_cutting    = data.get("cross_cutting", []),
            field_semantics  = data.get("field_semantics", {}),
            entity_catalog   = data.get("entity_catalog", []),
        )
