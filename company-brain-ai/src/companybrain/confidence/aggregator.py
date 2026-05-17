"""
MultiSignalAggregator — A1.4 Verbalized Confidence.

Combines six objective signals into a single confidence scalar and
delegates to Verbalizer for the human-readable label + rationale.

Weights are tunable via config (CONFIDENCE_WEIGHT_* env vars).
"""
from __future__ import annotations

from dataclasses import dataclass

from companybrain.confidence.signals import ConfidenceSignals
from companybrain.confidence.verbalizer import Verbalizer, VerbalizedConfidence

# Default weights — must sum to 1.0.
_DEFAULT_WEIGHTS: dict[str, float] = {
    "retrieval":     0.30,
    "entity_match":  0.20,
    "source_diversity": 0.15,
    "verifier":      0.20,
    "chain":         0.10,
    "freshness":     0.05,
}


@dataclass
class AggregatedConfidence:
    """
    Output of MultiSignalAggregator.aggregate().

    Attributes
    ----------
    value
        Weighted scalar in [0.0, 1.0].
    label
        Human-readable bucket: "high" | "medium" | "low".
    rationale
        Generated sentence referencing actual signal values.
    signals
        Raw signal dict for UI/debugging (matches ConfidenceSignals.as_dict()).
    weights
        Weight dict used during aggregation (for auditability).
    """

    value: float
    label: str
    rationale: str
    signals: dict[str, float]
    weights: dict[str, float]


class MultiSignalAggregator:
    """
    Combines ConfidenceSignals into a single confidence score.

    Parameters
    ----------
    weights
        Optional weight override dict.  Missing keys fall back to defaults.
        Weights are re-normalised if they don't sum to 1.0 so a partial
        override (e.g. only bumping verifier) never breaks the math.
    """

    def __init__(self, weights: dict[str, float] | None = None) -> None:
        self._weights = dict(_DEFAULT_WEIGHTS)
        if weights:
            for k, v in weights.items():
                if k in self._weights:
                    self._weights[k] = float(v)
        # Renormalise so we're always well-defined even if caller passes
        # partial overrides that break the sum-to-1 invariant.
        total = sum(self._weights.values())
        if total > 0 and abs(total - 1.0) > 1e-9:
            self._weights = {k: v / total for k, v in self._weights.items()}
        self._verbalizer = Verbalizer()

    @classmethod
    def from_config(cls) -> "MultiSignalAggregator":
        """
        Construct from app settings.

        Reads CONFIDENCE_WEIGHT_* values from the Settings singleton so that
        env-var overrides take effect without restarting the process.
        """
        try:
            from companybrain.config import settings
            weights = {
                "retrieval":      settings.confidence_weight_retrieval,
                "entity_match":   settings.confidence_weight_entity_match,
                "source_diversity": settings.confidence_weight_source_diversity,
                "verifier":       settings.confidence_weight_verifier,
                "chain":          settings.confidence_weight_chain,
                "freshness":      settings.confidence_weight_freshness,
            }
        except AttributeError:
            # Config not yet upgraded — fall back to defaults.
            weights = None
        return cls(weights=weights)

    def aggregate(self, signals: ConfidenceSignals) -> AggregatedConfidence:
        """
        Compute the weighted confidence scalar and verbalise it.

        The scalar is a weighted sum of all six normalised signal values.
        """
        w = self._weights
        normalised = {
            "retrieval":      signals.retrieval_score,
            "entity_match":   signals.normalised_entity_match,
            "source_diversity": signals.source_diversity,
            "verifier":       signals.verifier_agreement,
            "chain":          signals.normalised_chain_length,
            "freshness":      signals.freshness_score,
        }
        scalar = sum(normalised[k] * w[k] for k in w)
        # Clamp to [0, 1] for safety (floating-point drift).
        scalar = max(0.0, min(1.0, scalar))

        verbalized: VerbalizedConfidence = self._verbalizer.verbalize(
            scalar, signals
        )
        return AggregatedConfidence(
            value=round(scalar, 4),
            label=verbalized.label,
            rationale=verbalized.rationale,
            signals=signals.as_dict(),
            weights=dict(w),
        )
