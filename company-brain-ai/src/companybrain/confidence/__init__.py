"""
companybrain.confidence — A1.4 Verbalized Confidence + Multi-Signal Aggregator.

Public API:

    from companybrain.confidence import (
        ConfidenceSignals,
        MultiSignalAggregator,
        AggregatedConfidence,
        build_confidence_from_query_result,
    )
"""
from companybrain.confidence.signals import ConfidenceSignals
from companybrain.confidence.aggregator import AggregatedConfidence, MultiSignalAggregator
from companybrain.confidence.verbalizer import Verbalizer, VerbalizedConfidence
from companybrain.confidence.helpers import build_confidence_from_query_result

__all__ = [
    "ConfidenceSignals",
    "MultiSignalAggregator",
    "AggregatedConfidence",
    "Verbalizer",
    "VerbalizedConfidence",
    "build_confidence_from_query_result",
]
