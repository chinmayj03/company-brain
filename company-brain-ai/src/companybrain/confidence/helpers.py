"""
helpers — A1.4 Verbalized Confidence.

Utility functions for extracting ConfidenceSignals from a QueryResponse and
producing an updated Confidence model ready for the API response.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from companybrain.models.query_response import Confidence, QueryResponse
    from companybrain.confidence.signals import ConfidenceSignals


def signals_from_response(
    response: "QueryResponse",
    *,
    retrieval_score: float = 0.0,
    source_paths: list[str] | None = None,
    verifier_score: float | None = None,
    freshness_score: float = 0.5,
) -> "ConfidenceSignals":
    """
    Build a ConfidenceSignals object from a QueryResponse and optional metadata.

    Parameters
    ----------
    response
        The QueryResponse produced by the answerer/exploration loop.
    retrieval_score
        Top RRF score from the retrieval step (0.0 if unavailable).
    source_paths
        List of file paths in the assembled context, used to compute
        source_diversity.  Pass an empty list or None if not available.
    verifier_score
        Raw verifier score from SelfVerifier, or None if the loop didn't run.
        Mapped to: ≥ threshold → 1.0, < threshold → 0.0, None → 0.5.
    freshness_score
        Freshness of the retrieved entities (0.0–1.0).
        Stubbed at 0.5 until per-entity timestamps are available.
    """
    from companybrain.confidence.signals import ConfidenceSignals

    # ── source diversity ──────────────────────────────────────────────────
    paths = source_paths or []
    if paths:
        unique = len(set(paths))
        source_diversity = unique / max(len(paths), 1)
    else:
        source_diversity = 0.5  # no paths → stub at mid

    # ── verifier agreement ────────────────────────────────────────────────
    if verifier_score is None:
        verifier_agreement = 0.5
    else:
        try:
            from companybrain.config import settings
            threshold = settings.iterative_verifier_score_threshold
        except Exception:
            threshold = 0.6
        verifier_agreement = 1.0 if verifier_score >= threshold else 0.0

    # ── entity count ──────────────────────────────────────────────────────
    entity_count = len(getattr(response, "affected_entities", []) or [])

    # ── chain length ──────────────────────────────────────────────────────
    chain_len = len(getattr(response, "call_chain", []) or [])

    return ConfidenceSignals(
        retrieval_score=max(0.0, min(1.0, float(retrieval_score))),
        entity_match_count=entity_count,
        source_diversity=max(0.0, min(1.0, float(source_diversity))),
        verifier_agreement=verifier_agreement,
        chain_length=chain_len,
        freshness_score=max(0.0, min(1.0, float(freshness_score))),
    )


def build_confidence_from_query_result(
    response: "QueryResponse",
    *,
    retrieval_score: float = 0.0,
    source_paths: list[str] | None = None,
    verifier_score: float | None = None,
    freshness_score: float = 0.5,
) -> "Confidence":
    """
    Compute an updated Confidence object for a QueryResponse.

    Replaces the LLM-produced confidence stub with a deterministic
    multi-signal score. Keeps backward-compat fields (level, rationale)
    and adds new fields (value, signals).

    Returns a new ``Confidence`` instance — callers should replace
    ``response.confidence`` with the returned value.
    """
    from companybrain.models.query_response import Confidence
    from companybrain.confidence.aggregator import MultiSignalAggregator

    sigs = signals_from_response(
        response,
        retrieval_score=retrieval_score,
        source_paths=source_paths,
        verifier_score=verifier_score,
        freshness_score=freshness_score,
    )
    agg = MultiSignalAggregator.from_config()
    result = agg.aggregate(sigs)

    return Confidence(
        level=result.label,          # type: ignore[arg-type]
        rationale=result.rationale,
        value=result.value,
        signals=result.signals,
    )
