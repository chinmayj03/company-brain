"""
Base formatter — ADR-0079 M4.

Defines the AnswerBlock container and the BaseFormatter ABC.
Every persona formatter inherits from BaseFormatter and implements format().
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

from companybrain.personas.shape import AnswerFormat, QuestionShape


@dataclass
class AnswerBlock:
    """A single named section of a persona-formatted answer."""
    section: str
    content: str
    citations: list[dict] = field(default_factory=list)   # [{urn, name, why_relevant}]
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FormattedAnswer:
    """
    The complete structured output from a persona formatter.

    answer_blocks: list of named sections (persona-specific layout)
    raw_text:      plain-text join for backwards-compat with summary field
    persona:       which persona produced this
    shape_id:      which template shape was matched
    match_confidence: confidence of the shape match
    fell_through_to_generic: True if no shape matched
    """
    answer_blocks: list[AnswerBlock] = field(default_factory=list)
    raw_text: str = ""
    persona: str = ""
    shape_id: Optional[str] = None
    match_confidence: float = 0.0
    fell_through_to_generic: bool = False
    bindings_applied: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "persona": self.persona,
            "matched_shape_id": self.shape_id,
            "match_confidence": self.match_confidence,
            "fell_through_to_generic": self.fell_through_to_generic,
            "answer_blocks": [
                {
                    "section": b.section,
                    "content": b.content,
                    "citations": b.citations,
                    "metadata": b.metadata,
                }
                for b in self.answer_blocks
            ],
        }


class BaseFormatter(ABC):
    """
    Abstract base for persona answer formatters.

    Each formatter:
      1. Receives a raw answer (string from the LLM) + the matched QuestionShape
         + optional vertical bindings (dict from the bindings YAML).
      2. Parses / re-structures the raw answer into persona-specific AnswerBlocks.
      3. Returns a FormattedAnswer.

    Formatters are intentionally simple: they reformat existing LLM output,
    not re-query the LLM. The shape's answer_format drives which sections
    to emit and how to style citations.
    """

    persona: str = "generic"

    def format(
        self,
        raw_answer: str,
        shape: Optional[QuestionShape],
        bindings: Optional[dict] = None,
        match_confidence: float = 0.0,
        fell_through_to_generic: bool = False,
    ) -> FormattedAnswer:
        """
        Public entry point. Dispatches to _format_shaped or _format_generic.
        """
        if shape is None or fell_through_to_generic:
            return self._format_generic(raw_answer)

        blocks = self._format_shaped(raw_answer, shape, bindings or {})
        raw = "\n\n".join(
            f"### {b.section}\n{b.content}" for b in blocks
        )
        return FormattedAnswer(
            answer_blocks=blocks,
            raw_text=raw,
            persona=self.persona,
            shape_id=shape.id,
            match_confidence=match_confidence,
            fell_through_to_generic=False,
            bindings_applied=bindings or {},
        )

    def _format_generic(self, raw_answer: str) -> FormattedAnswer:
        """Wrap a raw answer in a single generic block."""
        block = AnswerBlock(
            section="answer",
            content=raw_answer,
        )
        return FormattedAnswer(
            answer_blocks=[block],
            raw_text=raw_answer,
            persona=self.persona,
            shape_id=None,
            match_confidence=0.0,
            fell_through_to_generic=True,
        )

    @abstractmethod
    def _format_shaped(
        self,
        raw_answer: str,
        shape: QuestionShape,
        bindings: dict,
    ) -> list[AnswerBlock]:
        """
        Produce persona-specific AnswerBlocks from the raw LLM answer.

        Subclasses:
        - Parse sections from the raw text using the shape's answer_format.sections list
        - Attach citations per the citation_style
        - Return blocks in order (required sections first; optional if content found)
        """
        ...

    # ── Shared helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _extract_citations_from_text(text: str) -> list[dict]:
        """
        Extract URN citations from text.
        Looks for patterns like [urn:cb:...] or urn:cb:...
        """
        import re
        URN_RE = re.compile(r'urn:cb:[a-zA-Z0-9:._\-]{5,120}')
        seen: set[str] = set()
        citations: list[dict] = []
        for m in URN_RE.finditer(text):
            urn = m.group(0).rstrip(".,;)\"'")
            if urn not in seen:
                seen.add(urn)
                name = urn.rsplit(":", 1)[-1]
                citations.append({
                    "urn": urn,
                    "name": name,
                    "why_relevant": "cited in answer",
                })
        return citations[:10]

    @staticmethod
    def _split_into_paragraphs(text: str, max_paragraphs: int = 5) -> list[str]:
        """Split raw text into logical paragraphs."""
        paras = [p.strip() for p in text.split("\n\n") if p.strip()]
        return paras[:max_paragraphs]

    @staticmethod
    def _first_n_sentences(text: str, n: int = 3) -> str:
        """Extract the first N sentences from text."""
        import re
        sentences = re.split(r'(?<=[.!?])\s+', text.strip())
        return " ".join(sentences[:n])
