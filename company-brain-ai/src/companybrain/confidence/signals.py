"""
ConfidenceSignals — A1.4 Verbalized Confidence.

Six objective signals collected after retrieval + answer generation.
These are combined by MultiSignalAggregator into a scalar confidence score.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ConfidenceSignals:
    """
    Raw signals collected for a single query answer.

    All values are normalised to [0.0, 1.0] before aggregation.

    Attributes
    ----------
    retrieval_score
        RRF-combined BM25+dense retrieval score of the top hit, or the mean
        score across all hits when multiple were retrieved.
        Range: 0.0 (no hits) → 1.0 (perfect match).
    entity_match_count
        Number of distinct entities cited in the final answer
        (``affected_entities`` length).  Normalised as ``min(count / 5, 1.0)``
        so 5+ cited entities map to 1.0.
    source_diversity
        Fraction of unique file paths in the retrieved context.
        ``len(unique_paths) / max(len(all_paths), 1)``.
        1.0 = every chunk came from a different file (high diversity).
    verifier_agreement
        1.0 if the self-verifier loop ran and confirmed the answer,
        0.5 if the verifier loop was not run (default),
        0.0 if the verifier found issues (score < threshold).
    chain_length
        Number of hops in the causal / call chain used to construct the
        answer.  Normalised as ``min(hops / 5, 1.0)``.
    freshness_score
        1.0 if the entities were extracted today, decaying to 0.0 at 90 days.
        Stubbed at 0.5 pending per-entity timestamp availability in the store.
    """

    retrieval_score: float = 0.0
    entity_match_count: int = 0
    source_diversity: float = 0.5
    verifier_agreement: float = 0.5
    chain_length: int = 0
    freshness_score: float = 0.5  # stubbed — see docstring

    # ── Derived normalised values ─────────────────────────────────────────

    @property
    def normalised_entity_match(self) -> float:
        """entity_match_count normalised to [0, 1] (cap at 5 entities)."""
        return min(self.entity_match_count / 5.0, 1.0)

    @property
    def normalised_chain_length(self) -> float:
        """chain_length normalised to [0, 1] (cap at 5 hops)."""
        return min(self.chain_length / 5.0, 1.0)

    def as_dict(self) -> dict[str, float]:
        """Return all raw signal values as a plain dict (for JSON output)."""
        return {
            "retrieval_score": self.retrieval_score,
            "entity_match_count": float(self.entity_match_count),
            "source_diversity": self.source_diversity,
            "verifier_agreement": self.verifier_agreement,
            "chain_length": float(self.chain_length),
            "freshness_score": self.freshness_score,
        }
