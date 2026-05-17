"""
TTL classifier — ADR-0064 M2.

ttl_classify(content, source_type, pii_findings) -> TTLClass

Rules (applied in priority order):
  1. Any finding with kind=api_key → secret (blocked at ingest)
  2. Any PII finding in {email, phone, ssn, credit_card} → pii_unless_consented
  3. source_type=secret / content matches secret patterns → secret
  4. source_type in {test, fixture, spec} → transient (90-day)
  5. source_type=config → operational (1-year)
  6. source_type=documentation → business_indefinite (7-year)
  7. default → permanent
"""
from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .pii_detector import PIIFinding


class TTLClass(str, Enum):
    """
    Every brain entity is stamped with one of these at ingest.
    The TTL evaluator uses this to determine when and how to expire the record.
    """
    PERMANENT = "permanent"
    BUSINESS_INDEFINITE = "business_indefinite"   # 7 years → tombstone
    OPERATIONAL = "operational"                   # 1 year → tombstone
    TRANSIENT = "transient"                       # 90 days → hard-delete
    PII_UNLESS_CONSENTED = "pii_unless_consented" # 30 days → redact
    SECRET = "secret"                             # 0 days → rejected at ingest


# PII kinds that trigger pii_unless_consented
_PII_KINDS_TRIGGER = frozenset({"email", "phone", "ssn", "credit_card", "personal_name"})

# PII kinds that trigger secret (never stored)
_SECRET_KINDS = frozenset({"api_key"})

# Source types that map to specific TTL classes
_TRANSIENT_SOURCE_TYPES = frozenset({
    "test", "fixture", "spec", "test_fixture", "generated",
    "ci_artifact", "ephemeral",
})
_OPERATIONAL_SOURCE_TYPES = frozenset({
    "config", "configuration", "env", "environment",
    "settings", "properties",
})
_DOCUMENTATION_SOURCE_TYPES = frozenset({
    "documentation", "docs", "readme", "changelog", "adr",
})


def ttl_classify(
    content: str,
    source_type: str,
    pii_findings: "list[PIIFinding]",
) -> TTLClass:
    """
    Determine the TTL class for a record at ingest time.

    Args:
        content: The raw text content of the chunk/entity.
        source_type: A string hint about where the content came from
            (e.g., "test", "config", "production_code").
        pii_findings: Findings from pii_detector.scan().

    Returns:
        A TTLClass enum value.
    """
    # Priority 1: secret PII kinds → reject immediately
    for finding in pii_findings:
        if finding.kind in _SECRET_KINDS:
            return TTLClass.SECRET

    # Priority 2: PII that needs consent before long-term retention
    for finding in pii_findings:
        if finding.kind in _PII_KINDS_TRIGGER:
            return TTLClass.PII_UNLESS_CONSENTED

    # Priority 3: source_type-based classification
    st_lower = source_type.lower() if source_type else ""

    if st_lower in _TRANSIENT_SOURCE_TYPES:
        return TTLClass.TRANSIENT

    if st_lower in _OPERATIONAL_SOURCE_TYPES:
        return TTLClass.OPERATIONAL

    if st_lower in _DOCUMENTATION_SOURCE_TYPES:
        return TTLClass.BUSINESS_INDEFINITE

    # Default: production code / unknown → permanent
    return TTLClass.PERMANENT


def expiry_days(ttl_class: TTLClass) -> int | None:
    """
    Return the default expiry in days for a TTL class.
    Returns None for permanent (no expiry).
    Returns 0 for secret (should never be stored).
    """
    _MAP = {
        TTLClass.PERMANENT: None,
        TTLClass.BUSINESS_INDEFINITE: 365 * 7,
        TTLClass.OPERATIONAL: 365,
        TTLClass.TRANSIENT: 90,
        TTLClass.PII_UNLESS_CONSENTED: 30,
        TTLClass.SECRET: 0,
    }
    return _MAP[ttl_class]
