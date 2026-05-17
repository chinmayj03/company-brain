"""
Privacy package — ADR-0064 M1 + M2.

PII detection (M1):
    from companybrain.privacy import scan, PIIFinding

TTL classification (M2):
    from companybrain.privacy import ttl_classify, TTLClass

TTL evaluation / sweep (M2):
    from companybrain.privacy import sweep, SweepResult
"""

from .pii_detector import PIIFinding, scan, scan_async, clear_cache
from .ttl_classifier import TTLClass, ttl_classify, expiry_days
from .ttl_evaluator import sweep, SweepResult, redact_pii

__all__ = [
    # PII detection
    "PIIFinding",
    "scan",
    "scan_async",
    "clear_cache",
    # TTL classification
    "TTLClass",
    "ttl_classify",
    "expiry_days",
    # TTL evaluation
    "sweep",
    "SweepResult",
    "redact_pii",
]
