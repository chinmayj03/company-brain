"""
PM persona formatter — ADR-0079 M4.

Default sections per ADR-0079 M4 table:
  status_summary | milestones_hit | milestones_missed | blocking_items | estimate_assessment

Citation style: section_level (2-4 per claim; grouped at section level).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from companybrain.personas.formatters.base import AnswerBlock, BaseFormatter
from companybrain.personas.formatters.developer import (
    _best_section_match,
    _extract_labelled_sections,
)
from companybrain.personas.shape import QuestionShape

log = logging.getLogger(__name__)

# Section aliases for PM-specific sections
_PM_SECTION_ALIASES: dict[str, list[str]] = {
    "status_summary": [
        "status", "summary", "current status", "overview", "where we are",
        "progress", "state",
    ],
    "milestones_hit": [
        "completed", "done", "shipped", "milestones hit", "achieved",
        "delivered", "what's complete", "what we've done",
    ],
    "milestones_missed": [
        "missed", "delayed", "not done", "outstanding", "pending",
        "milestones missed", "what's late", "behind schedule",
    ],
    "blocking_items": [
        "blocking", "blockers", "blocked", "impediments", "open decisions",
        "unresolved", "pending decisions", "what's blocking",
    ],
    "estimate_assessment": [
        "estimate", "timeline", "eta", "forecast", "when", "ship date",
        "delivery estimate", "confidence", "on track",
    ],
    "entity_map": [
        "entities", "affected", "touches", "scope", "domain", "services",
        "what does it touch",
    ],
    "commitments_summary": [
        "commitments", "promises", "promised", "customer", "told", "said",
        "what we committed", "customer promise",
    ],
    "shipped_items": [
        "shipped", "delivered", "released", "launched", "what shipped",
        "recent releases",
    ],
    "in_progress": [
        "in progress", "active", "current work", "ongoing", "working on",
    ],
    "planned": [
        "planned", "upcoming", "next", "roadmap", "future", "scheduled",
    ],
    "citations": [
        "citations", "sources", "references", "evidence",
    ],
}


class PMFormatter(BaseFormatter):
    """Answer formatter for the PM persona."""

    persona = "pm"

    def _format_shaped(
        self,
        raw_answer: str,
        shape: QuestionShape,
        bindings: dict,
    ) -> list[AnswerBlock]:
        """
        Parse the LLM's raw answer into PM-specific sections.

        PM layout varies by shape:
          pm.feature_progress:            status_summary, milestones_hit, milestones_missed,
                                          blocking_items, citations
          pm.entities_affected_by_feature: entity_map, service_changes, payer_impact, citations
          pm.open_decisions_for_feature:  blocking_decisions, open_questions, citations
          pm.roadmap_status:              shipped_items, in_progress, planned, citations
          pm.customer_promise_lookup:     commitments_summary, source_details, citations

        Citation style: section_level — 2-4 citations per section, not per sentence.
        """
        desired_sections = [s.name for s in shape.answer_format.sections]

        # Merge the PM section aliases with the shape's desired sections
        all_aliases = {**_PM_SECTION_ALIASES}

        extracted = _extract_labelled_sections(raw_answer)
        blocks: list[AnswerBlock] = []

        if extracted:
            used_sections: set[str] = set()
            for heading, content in extracted:
                heading_words = set(re.findall(r'\b\w+\b', heading.lower()))
                best: Optional[str] = None
                best_score = 0
                for section in desired_sections:
                    aliases = all_aliases.get(section, [section])
                    for alias in aliases:
                        alias_words = set(re.findall(r'\b\w+\b', alias.lower()))
                        overlap = len(heading_words & alias_words)
                        if overlap > best_score:
                            best_score = overlap
                            best = section

                if best and best not in used_sections:
                    cits = self._extract_citations_from_text(content)
                    blocks.append(AnswerBlock(
                        section=best,
                        content=content,
                        citations=cits,
                        metadata={
                            "citation_style": "section_level",
                            "chart_hint": shape.answer_format.chart_types,
                        },
                    ))
                    used_sections.add(best)

            # Fill required sections that weren't extracted
            for sec_spec in shape.answer_format.sections:
                if sec_spec.required and sec_spec.name not in used_sections:
                    if sec_spec.name != "citations":
                        blocks.append(AnswerBlock(
                            section=sec_spec.name,
                            content="No specific information available for this section.",
                            citations=[],
                        ))
        else:
            # Flat text: assign paragraphs to sections
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
                    metadata={"citation_style": "section_level"},
                ))

            # PM aggregates all citations at the end
            if all_citations:
                blocks.append(AnswerBlock(
                    section="citations",
                    content="\n".join(
                        f"- [{c['urn']}] {c['name']}" for c in all_citations
                    ),
                    citations=all_citations,
                ))

        # Apply vertical bindings: add domain_callouts if present
        binding_callouts = _get_binding_callouts(shape.id, bindings)
        if binding_callouts:
            blocks.append(AnswerBlock(
                section="domain_callouts",
                content="\n".join(f"- {note}" for note in binding_callouts),
                citations=[],
                metadata={"source": "vertical_binding"},
            ))

        # Ensure citations block exists
        all_cits = self._extract_citations_from_text(raw_answer)
        has_citation_block = any(b.section == "citations" for b in blocks)
        if all_cits and not has_citation_block:
            blocks.append(AnswerBlock(
                section="citations",
                content="\n".join(
                    f"- [{c['urn']}] {c['name']}" for c in all_cits
                ),
                citations=all_cits,
            ))

        log.debug(
            "[pm-formatter] %s → %d blocks (binding_callouts=%d)",
            shape.id,
            len(blocks),
            len(binding_callouts),
        )
        return blocks


def _get_binding_callouts(shape_id: str, bindings: dict) -> list[str]:
    """Extract domain_callouts from vertical bindings for a shape."""
    if not bindings:
        return []
    shape_bindings = bindings.get(shape_id, {})
    return shape_bindings.get("domain_callouts", [])
