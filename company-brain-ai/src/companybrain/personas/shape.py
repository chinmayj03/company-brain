"""
QuestionShape schema — ADR-0079 M1.

Every persona template is a typed Shape: a structured definition of
what question it answers, what signals it needs, how to retrieve them,
and how to format the answer. Shapes are vertical-agnostic; vertical
bindings (M2) fill in the entity slots.

Shapes are first-class typed objects so the refinement loop (ADR-0066)
and the curation UI (ADR-0083) can promote/demote/version them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional


PersonaLiteral = Literal["dev", "pm", "cs", "vp_eng", "cfo", "ceo"]


@dataclass
class SparseFallback:
    """Declares what to do when a required signal is missing."""
    strategy: Literal["use_partial", "skip_section", "refuse", "generic_retrieval"]
    message: str = ""


@dataclass
class SignalSpec:
    """Specification for a single data signal required (or optional) by a shape."""
    name: str
    # Which data views / indexes to look in for this signal.
    source_views: list[str] = field(default_factory=list)
    # Minimum confidence to consider the signal satisfied (0.0-1.0).
    required_confidence: float = 0.6
    sparse_fallback: SparseFallback = field(
        default_factory=lambda: SparseFallback(strategy="generic_retrieval")
    )


@dataclass
class RetrievalRecipe:
    """Specifies which retrieval strategy to use for a shape."""
    # Primary retrieval strategy identifier.
    strategy: Literal[
        "hybrid_search",        # BM25 + dense vector (default)
        "timeline_view",        # event-stream temporal window
        "blast_radius",         # call-graph expansion
        "causal_chain",         # decision→code→outcome chain
        "entity_lookup",        # direct entity resolution
        "cross_source_search",  # multi-source join (calls, PRDs, code)
        "drift_check",          # ADR vs code drift detection
        "tech_debt_scan",       # TODO/FIXME/antipattern scan
        "git_provenance",       # git blame + authorship
    ]
    # Additional hints passed to the retrieval layer.
    hints: dict[str, str] = field(default_factory=dict)
    # Override the default Qdrant index.
    qdrant_index: str = "default"
    # Token budget for raw evidence (before answer synthesis).
    evidence_budget_tokens: int = 4000


@dataclass
class SectionSpec:
    """Specification for a single section in the answer format."""
    name: str
    required: bool = True
    description: str = ""


@dataclass
class AnswerFormat:
    """Specifies how the answer should be structured and rendered."""
    sections: list[SectionSpec] = field(default_factory=list)
    # Citation density hint per claim.
    citation_min: int = 1
    citation_max: int = 4
    # How citations are anchored (line-level for dev; section-level for PM).
    citation_style: Literal["line_anchored", "section_level", "aggregate"] = "section_level"
    # Whether to include chart type hints.
    chart_types: list[str] = field(default_factory=list)


@dataclass
class FallbackPolicy:
    """What to do when shape matching fails or signals are sparse."""
    on_no_match: Literal["generic_retrieval", "refuse", "suggest_shapes"] = "generic_retrieval"
    on_sparse_signals: Literal["partial_answer", "refuse", "generic_retrieval"] = "partial_answer"
    min_signal_coverage: float = 0.3


@dataclass
class RefinementMeta:
    """Metadata used by the refinement loop (ADR-0066/0067)."""
    promoted_at: Optional[str] = None    # ISO datetime string
    usage_count: int = 0
    match_score_avg: float = 0.0
    follow_up_rate: float = 0.0          # fraction of uses that needed a follow-up
    last_used: Optional[str] = None


@dataclass
class QuestionShape:
    """
    A fully-typed persona template shape.

    Shapes are vertical-agnostic: they describe the *structure* of a question
    and the *format* of the answer. Vertical bindings fill in the concrete
    entity references (see bindings/healthcare-rcm.yaml).

    id convention: "<persona>.<shape_name>" e.g. "dev.blast_radius"
    """
    id: str
    persona: PersonaLiteral
    intent: str                           # Natural-language description of the intent
    intent_examples: list[str]            # 3-10 example queries for router matching
    required_signals: list[SignalSpec] = field(default_factory=list)
    optional_signals: list[SignalSpec] = field(default_factory=list)
    retrieval_recipe: RetrievalRecipe = field(
        default_factory=lambda: RetrievalRecipe(strategy="hybrid_search")
    )
    answer_format: AnswerFormat = field(default_factory=AnswerFormat)
    evidence_budget_tokens: int = 4000
    fallback_behavior: FallbackPolicy = field(default_factory=FallbackPolicy)
    refinement_metadata: RefinementMeta = field(default_factory=RefinementMeta)

    def validate(self) -> list[str]:
        """Return a list of validation errors; empty list means valid."""
        errors: list[str] = []
        if not self.id:
            errors.append("id is required")
        if "." not in self.id:
            errors.append(f"id must be '<persona>.<shape>' — got: {self.id!r}")
        if not self.persona:
            errors.append("persona is required")
        if not self.intent:
            errors.append(f"{self.id}: intent is required")
        if len(self.intent_examples) < 1:
            errors.append(f"{self.id}: at least 1 intent_example is required")
        if self.answer_format is None:
            errors.append(f"{self.id}: answer_format is required")
        return errors
