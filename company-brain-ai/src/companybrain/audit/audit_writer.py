"""
AuditWriter — thin synchronous wrapper over AuditLog.

Provides sensible defaults for actor and workspace so call-sites don't
need to thread those through every mutation.  Synchronous by design:
the audit write must complete before the caller returns so there's no
window where a mutation exists without an audit record.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .audit_log import AuditLog, AuditEntry


class AuditWriter:
    """
    Synchronous audit recorder.

    Usage:
        writer = AuditWriter(log, default_actor="pipeline", default_workspace="acme")
        entry = writer.record("entity_create", target_urn="urn:cb:...", diff={...})
    """

    def __init__(
        self,
        log: AuditLog,
        default_actor: str = "system",
        default_workspace: str = "default",
    ) -> None:
        self._log = log
        self._default_actor = default_actor
        self._default_workspace = default_workspace

    def record(
        self,
        op: str,
        *,
        target_urn: Optional[str] = None,
        diff: Optional[dict] = None,
        rationale: Optional[str] = None,
        actor: Optional[str] = None,
        workspace: Optional[str] = None,
        timestamp_utc: Optional[datetime] = None,
    ) -> AuditEntry:
        """
        Append one audit entry.  Returns the completed AuditEntry with
        seq and self_hash populated.

        All parameters except op are optional; sensible defaults are used.
        """
        return self._log.append(
            actor=actor or self._default_actor,
            workspace=workspace or self._default_workspace,
            op=op,
            target_urn=target_urn,
            diff=diff,
            rationale=rationale,
            timestamp_utc=timestamp_utc,
        )

    @property
    def log(self) -> AuditLog:
        return self._log
