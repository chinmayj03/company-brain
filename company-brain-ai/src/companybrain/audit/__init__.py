"""
Audit package — hash-chained tamper-evident log for all brain mutations.

ADR-0064 M3: every entity create/update/delete and every PII/TTL action
is appended to an append-only JSONL log with a SHA-256 hash chain.
Tampering with any past entry breaks the chain.

Quick start:
    from companybrain.audit import AuditLog, AuditWriter, AuditQuery, AuditEntry

    log = AuditLog(path=Path("audit.jsonl"))
    writer = AuditWriter(log, default_actor="pipeline", default_workspace="default")
    writer.record("entity_create", target_urn="urn:cb:...", diff={"after": {...}})

    result = log.verify_chain()
    assert result.is_valid
"""

from .audit_log import AuditLog, AuditEntry, ChainVerifyResult
from .audit_writer import AuditWriter
from .audit_query import AuditQuery

__all__ = [
    "AuditLog",
    "AuditEntry",
    "ChainVerifyResult",
    "AuditWriter",
    "AuditQuery",
]
