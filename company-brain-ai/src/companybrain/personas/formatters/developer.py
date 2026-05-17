"""
Developer persona formatter — ADR-0079 M4.

Default sections per ADR-0079 M4 table:
  blast_radius | similar_implementations | risk_overlay | citations

Citation style: line_anchored (1-3 per claim; close to the relevant code reference).
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from companybrain.personas.formatters.base import AnswerBlock, BaseFormatter
from companybrain.personas.shape import QuestionShape

log = logging.getLogger(__name__)

# Section headers the LLM may produce (or we synthesise from raw).
_SECTION_ALIASES: dict[str, list[str]] = {
    "blast_radius": [
        "blast radius", "blast_radius", "affected", "impact", "dependencies",
        "dependents", "downstream", "breaking", "what breaks",
    ],
    "similar_implementations": [
        "similar", "implementation", "pattern", "existing", "examples",
        "how we do", "approach", "recommended",
    ],
    "domain_definition": [
        "definition", "domain", "meaning", "what is", "entity", "concept",
        "business meaning", "technical role",
    ],
    "decision_summary": [
        "decision", "rationale", "why", "reason", "motivation", "constraint",
        "background", "context",
    ],
    "ownership_summary": [
        "owner", "ownership", "who", "author", "contributor", "team",
        "responsible",
    ],
    "risk_overlay": [
        "risk", "risk overlay", "change risk", "assessment", "impact level",
    ],
    "citations": [
        "citations", "sources", "references", "cited",
    ],
}


def _best_section_match(heading: str, sections: list[str]) -> Optional[str]:
    """Map a heading to the closest section name using keyword overlap."""
    heading_words = set(re.findall(r'\b\w+\b', heading.lower()))
    best: Optional[str] = None
    best_score = 0
    for section in sections:
        aliases = _SECTION_ALIASES.get(section, [section])
        for alias in aliases:
            alias_words = set(re.findall(r'\b\w+\b', alias.lower()))
            overlap = len(heading_words & alias_words)
            if overlap > best_score:
                best_score = overlap
                best = section
    return best if best_score > 0 else None


class DeveloperFormatter(BaseFormatter):
    """Answer formatter for the Developer persona."""

    persona = "dev"

    def _format_shaped(
        self,
        raw_answer: str,
        shape: QuestionShape,
        bindings: dict,
    ) -> list[AnswerBlock]:
        """
        Parse the LLM's raw answer into developer-specific sections.

        Developer layout varies by shape:
          dev.blast_radius:           blast_radius, risk_overlay, citations
          dev.similar_implementations: similar_implementations, risk_notes, citations
          dev.domain_meaning_of_entity: domain_definition, technical_role, citations
          dev.why_was_this_decided:    decision_summary, alternatives, constraints, citations
          dev.who_owns_this_area:      ownership_summary, recent_contributors, citations

        If the LLM didn't produce recognizable sections, we fall back to
        splitting into paragraphs and assigning them to sections in order.
        """
        desired_sections = [s.name for s in shape.answer_format.sections]

        # Try to extract labelled sections from the raw answer.
        extracted = _extract_labelled_sections(raw_answer)

        blocks: list[AnswerBlock] = []

        if extracted:
            # Map extracted headings to desired sections
            used_sections: set[str] = set()
            for heading, content in extracted:
                matched = _best_section_match(heading, desired_sections)
                if matched and matched not in used_sections:
                    cits = self._extract_citations_from_text(content)
                    blocks.append(AnswerBlock(
                        section=matched,
                        content=content,
                        citations=cits,
                        metadata={"citation_style": shape.answer_format.citation_style},
                    ))
                    used_sections.add(matched)

            # Any desired required sections that weren't matched get a fallback block
            for sec_spec in shape.answer_format.sections:
                if sec_spec.required and sec_spec.name not in used_sections:
                    blocks.append(AnswerBlock(
                        section=sec_spec.name,
                        content="No specific information available for this section.",
                        citations=[],
                    ))
        else:
            # Flat text: split into paragraphs and assign to sections in order
            paragraphs = self._split_into_paragraphs(raw_answer, max_paragraphs=6)
            all_citations = self._extract_citations_from_text(raw_answer)

            for i, sec_spec in enumerate(shape.answer_format.sections):
                if sec_spec.name == "citations":
                    continue
                content = paragraphs[i] if i < len(paragraphs) else (
                    "No specific information available." if sec_spec.required else ""
                )
                if not content:
                    continue
                block_cits = self._extract_citations_from_text(content)
                blocks.append(AnswerBlock(
                    section=sec_spec.name,
                    content=content,
                    citations=block_cits,
                    metadata={"citation_style": "line_anchored"},
                ))

            # Aggregate citations block at the end
            if all_citations:
                blocks.append(AnswerBlock(
                    section="citations",
                    content="\n".join(
                        f"- [{c['urn']}] {c['name']}" for c in all_citations
                    ),
                    citations=all_citations,
                ))

        # Always ensure citations section is present if we have any
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
            "[dev-formatter] %s → %d blocks (shape=%s)",
            shape.id,
            len(blocks),
            shape.id,
        )
        return blocks


def _extract_labelled_sections(text: str) -> list[tuple[str, str]]:
    """
    Extract (heading, content) pairs from markdown-style or plain-heading text.

    Recognises:
      ## Heading
      **Heading**:
      HEADING:
    """
    heading_re = re.compile(
        r'^(?:#{1,4}\s+(.+)|'            # ## Heading
        r'\*\*(.+?)\*\*\s*:?|'           # **Heading**
        r'([A-Z][A-Z\s_]{3,})\s*:)',     # HEADING:
        re.MULTILINE,
    )

    matches = list(heading_re.finditer(text))
    if not matches:
        return []

    result: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        heading = (m.group(1) or m.group(2) or m.group(3) or "").strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        content = text[start:end].strip()
        if heading and content:
            result.append((heading, content))
    return result
