"""
VP Engineering persona formatter — ADR-0079 M4.

Default sections per ADR-0079 M4 table:
  drift_summary | debt_hotspots | capacity_load | estimate_vs_actual

Citation style: aggregate — counts and drill-down links rather than
inline line-level citations. Each block gets a citation list for drill-down.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from companybrain.personas.formatters.base import AnswerBlock, BaseFormatter
from companybrain.personas.formatters.developer import _extract_labelled_sections
from companybrain.personas.shape import QuestionShape

log = logging.getLogger(__name__)

_VP_SECTION_ALIASES: dict[str, list[str]] = {
    "drift_summary": [
        "drift", "drifting", "diverging", "architectural drift", "where we drift",
        "divergence from", "intent vs reality",
    ],
    "worst_offenders": [
        "worst", "most severe", "top offenders", "highest drift", "worst areas",
    ],
    "trend_direction": [
        "trend", "trending", "improving", "worsening", "over time", "trajectory",
    ],
    "debt_hotspots": [
        "debt", "tech debt", "hotspots", "debt concentration", "worst debt",
        "technical debt", "areas with debt",
    ],
    "debt_categories": [
        "categories", "types", "breakdown", "by type", "classification",
    ],
    "priority_queue": [
        "priority", "priorities", "remediation", "what to fix first",
        "recommended order", "action items",
    ],
    "health_score": [
        "health", "health score", "overall health", "status", "health assessment",
        "red yellow green", "summary",
    ],
    "strengths": [
        "strengths", "strong", "working well", "positive", "good areas",
    ],
    "concerns": [
        "concerns", "risks", "issues", "problems", "gaps", "weak", "worrying",
    ],
    "bus_factor_risks": [
        "bus factor", "bus-factor", "single point", "knowledge risk",
        "critical single", "only one person",
    ],
    "medium_risk_areas": [
        "medium risk", "two people", "secondary risk", "watch list",
    ],
    "recent_changes_summary": [
        "recent changes", "recently changed", "what changed", "activity",
        "last 30 days", "recent activity",
    ],
    "significant_changes": [
        "significant", "major", "large", "high impact", "notable",
    ],
    "risk_signals": [
        "risk signals", "risky", "no tests", "missing coverage", "danger",
    ],
    "recommended_actions": [
        "recommended", "action", "suggestions", "what to do", "next steps",
    ],
    "citations": [
        "citations", "sources", "references", "evidence",
    ],
}


class VPEngFormatter(BaseFormatter):
    """Answer formatter for the VP Engineering persona."""

    persona = "vp_eng"

    def _format_shaped(
        self,
        raw_answer: str,
        shape: QuestionShape,
        bindings: dict,
    ) -> list[AnswerBlock]:
        """
        Parse the LLM's raw answer into VP Eng-specific sections.

        VP Eng layout varies by shape:
          vp.drift_trend:         drift_summary, worst_offenders, trend_direction,
                                  recommended_actions, citations
          vp.debt_hotspots:       debt_hotspots, debt_categories, velocity_impact,
                                  priority_queue, citations
          vp.area_health_summary: health_score, strengths, concerns, citations
          vp.bus_factor_per_area: bus_factor_risks, medium_risk_areas, recommended_actions,
                                  citations
          vp.recent_changes_to_area: recent_changes_summary, significant_changes,
                                      risk_signals, citations

        Citation style: aggregate — aggregated counts; drill-down links per block.
        """
        desired_sections = [s.name for s in shape.answer_format.sections]

        extracted = _extract_labelled_sections(raw_answer)
        blocks: list[AnswerBlock] = []

        if extracted:
            used_sections: set[str] = set()
            for heading, content in extracted:
                heading_words = set(re.findall(r'\b\w+\b', heading.lower()))
                best: Optional[str] = None
                best_score = 0
                for section in desired_sections:
                    aliases = _VP_SECTION_ALIASES.get(section, [section])
                    for alias in aliases:
                        alias_words = set(re.findall(r'\b\w+\b', alias.lower()))
                        overlap = len(heading_words & alias_words)
                        if overlap > best_score:
                            best_score = overlap
                            best = section

                if best and best not in used_sections:
                    cits = self._extract_citations_from_text(content)
                    # VP formatter aggregates citations with counts
                    blocks.append(AnswerBlock(
                        section=best,
                        content=content,
                        citations=cits,
                        metadata={
                            "citation_style": "aggregate",
                            "citation_count": len(cits),
                            "chart_hint": shape.answer_format.chart_types,
                        },
                    ))
                    used_sections.add(best)

            # Fill required sections
            for sec_spec in shape.answer_format.sections:
                if sec_spec.required and sec_spec.name not in used_sections:
                    if sec_spec.name != "citations":
                        blocks.append(AnswerBlock(
                            section=sec_spec.name,
                            content="No specific information available for this section.",
                            citations=[],
                        ))
        else:
            # Flat text: distribute paragraphs to sections
            paragraphs = self._split_into_paragraphs(raw_answer, max_paragraphs=6)
            all_citations = self._extract_citations_from_text(raw_answer)

            non_citation_sections = [
                s for s in shape.answer_format.sections if s.name != "citations"
            ]
            for i, sec_spec in enumerate(non_citation_sections):
                content = paragraphs[i] if i < len(paragraphs) else (
                    "No specific information available." if sec_spec.required else ""
                )
                if not content:
                    continue
                blocks.append(AnswerBlock(
                    section=sec_spec.name,
                    content=content,
                    citations=self._extract_citations_from_text(content),
                    metadata={
                        "citation_style": "aggregate",
                        "citation_count": len(self._extract_citations_from_text(content)),
                    },
                ))

            # VP summary citation block with aggregate count
            if all_citations:
                blocks.append(AnswerBlock(
                    section="citations",
                    content=(
                        f"{len(all_citations)} source(s) referenced.\n"
                        + "\n".join(f"- [{c['urn']}] {c['name']}" for c in all_citations)
                    ),
                    citations=all_citations,
                    metadata={"citation_count": len(all_citations)},
                ))

        # Ensure citations block present
        all_cits = self._extract_citations_from_text(raw_answer)
        has_citation_block = any(b.section == "citations" for b in blocks)
        if all_cits and not has_citation_block:
            blocks.append(AnswerBlock(
                section="citations",
                content=(
                    f"{len(all_cits)} source(s) referenced.\n"
                    + "\n".join(f"- [{c['urn']}] {c['name']}" for c in all_cits)
                ),
                citations=all_cits,
                metadata={"citation_count": len(all_cits)},
            ))

        log.debug(
            "[vp-formatter] %s → %d blocks", shape.id, len(blocks)
        )
        return blocks
