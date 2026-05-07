"""
Context Manager Agent — the 'OS' of the extraction layer.

Runs before every main entity-extraction LLM call.
Uses a fast/cheap model (FAST TaskRole = llama3.1:8b or Haiku) to:

  1. Profile the code unit (type, role, complexity)
  2. Decide which L2 categories are most relevant for THIS specific node
  3. Emit a system_prompt_patch — 1-2 sentences of domain-specific guidance
     derived from accumulated L2 knowledge
  4. Return a confidence_prior — affects whether main memory is fetched

The key output is system_prompt_patch:
  It injects domain knowledge into the extraction prompt that is ONLY
  available because of prior extractions in this run.
  e.g. "This service computes NIQ (Network IQ) competitiveness scores.
        PayerDataService is a known upstream dependency providing raw payer metrics."

Falls back to a deterministic rule-based assembly if the LLM call fails —
ensuring the pipeline is never blocked by CM Agent failures.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import structlog

from companybrain.collectors.code_tracer import CodeUnit
from companybrain.llm import get_provider, TaskRole, ChatMessage
from companybrain.config import settings
from companybrain.pipeline.context_hierarchy import L2SharedContext

log = structlog.get_logger(__name__)


@dataclass
class ContextAssembly:
    """
    Output of the Context Manager Agent for one extraction call.
    Consumed by EntityExtractor._extract_from_code_unit().
    """
    system_prompt_patch: str   # appended to the extraction system prompt
    l2_section: str            # pre-rendered L2 prompt section to inject in user message
    confidence_prior: str      # LOW | MEDIUM | HIGH — affects main memory fetch
    notes: str = ""            # CM Agent reasoning (for debug logs)


# ── Prompts ────────────────────────────────────────────────────────────────────

_CM_SYSTEM = """\
You are a Context Manager for an LLM code-extraction pipeline.
Your job: given a code unit's metadata and available workspace context, decide which
accumulated context is most relevant and write a 1-2 sentence guidance patch.

Respond ONLY with valid JSON (no markdown, no extra text):
{
  "system_prompt_patch": "<1-2 sentences of domain-specific guidance for extracting THIS unit>",
  "relevant_l2_keys": ["domain_glossary", "service_registry"],
  "confidence_prior": "LOW|MEDIUM|HIGH",
  "notes": "<one-line reasoning>"
}

relevant_l2_keys options: domain_glossary, service_registry, pattern_library, cross_cutting, field_semantics, entity_catalog
confidence_prior: LOW=simple DTO/model, MEDIUM=service/controller, HIGH=complex orchestrator or missing context\
"""


class ContextManagerAgent:
    """
    Assembles the optimal extraction prompt for each code unit.

    Call assemble() before every _extract_from_code_unit() call.
    If L2 is empty (first extraction in run), returns a no-op assembly immediately
    without making any LLM call.
    """

    def __init__(self):
        self._provider = get_provider()

    async def assemble(
        self,
        unit: CodeUnit,
        l2: L2SharedContext,
        endpoint: str,
        method: str,
    ) -> ContextAssembly:
        """
        Always uses rule-based assembly (zero LLM calls).

        The LLM-based path (_llm_assemble) adds ~90s per code unit on a local
        model and provides marginal improvement over deterministic rules when L2
        context is small (early pipeline runs).  Rule-based is fast and reliable.
        """
        if l2.is_empty():
            return ContextAssembly(
                system_prompt_patch="",
                l2_section="",
                confidence_prior="MEDIUM",
                notes="L2 empty — first extraction in this run",
            )

        return self._rule_based_assemble(unit, l2)

    # ── LLM-based assembly ─────────────────────────────────────────────────────

    async def _llm_assemble(
        self,
        unit: CodeUnit,
        l2: L2SharedContext,
        endpoint: str,
        method: str,
    ) -> ContextAssembly:
        user_content = f"""\
Code unit to extract from:
- Role: {unit.role}
- Class: {unit.class_name or 'unknown'}
- File: {unit.file_path}
- Language: {unit.language}
- Endpoint: {method} {endpoint}
- First 300 chars of source:
{(unit.content or '')[:300]}

Available workspace context (L2 summary):
{l2.compact_summary()}

Write the system_prompt_patch to guide extraction of this specific {unit.role}.\
"""

        raw = await self._provider.chat_json(
            messages=[
                ChatMessage(role="system", content=_CM_SYSTEM),
                ChatMessage(role="user",   content=user_content),
            ],
            role=TaskRole.FAST,
            max_tokens=settings.max_tokens_context_synthesis,
        )

        data = json.loads(raw)

        relevant_keys: list[str] = data.get("relevant_l2_keys", [])
        l2_section = l2.to_prompt_section(
            node_role=unit.role,
            budget_chars=1800,
            focus_keys=relevant_keys if relevant_keys else None,
        )

        patch    = data.get("system_prompt_patch", "").strip()
        prior    = data.get("confidence_prior", "MEDIUM")
        notes    = data.get("notes", "")

        log.debug(
            "CM Agent assembled context",
            unit=unit.class_name or unit.file_path,
            patch_len=len(patch),
            l2_keys=relevant_keys,
            confidence_prior=prior,
            notes=notes,
        )

        return ContextAssembly(
            system_prompt_patch=patch,
            l2_section=l2_section,
            confidence_prior=prior,
            notes=notes,
        )

    # ── Rule-based fallback ────────────────────────────────────────────────────

    def _rule_based_assemble(self, unit: CodeUnit, l2: L2SharedContext) -> ContextAssembly:
        """
        Deterministic fallback — no LLM call.
        Injects the role-appropriate L2 section and builds a template patch from L2 content.
        """
        l2_section = l2.to_prompt_section(node_role=unit.role, budget_chars=1800)

        patches: list[str] = []

        # Domain vocabulary hint
        if l2.domain_glossary:
            top3 = list(l2.domain_glossary.items())[:3]
            abbrevs = "; ".join(f"{k}={v[:30]}" for k, v in top3)
            patches.append(f"Domain abbreviations used in this codebase: {abbrevs}.")

        # If this exact class is in the service registry, say so
        class_name = unit.class_name or ""
        if class_name and class_name in l2.service_registry:
            entry = l2.service_registry[class_name]
            patches.append(
                f"Note: {class_name} has been identified as a '{entry['role']}' in this workspace."
            )

        # Architecture pattern hint
        if l2.pattern_library:
            patches.append(f"Detected architecture patterns: {l2.pattern_library[0]}.")

        # Cross-cutting hint
        if l2.cross_cutting:
            patches.append(f"Cross-cutting concerns active in this codebase: {l2.cross_cutting[0]}.")

        patch = " ".join(patches)

        # Confidence heuristic
        if unit.role in ("model", "dto", "schema"):
            prior = "LOW"
        elif unit.role in ("service", "controller", "handler"):
            prior = "MEDIUM"
        else:
            prior = "MEDIUM"

        return ContextAssembly(
            system_prompt_patch=patch,
            l2_section=l2_section,
            confidence_prior=prior,
            notes="rule-based fallback (CM Agent LLM call failed)",
        )
