"""
AuditQuery — filter and paginate audit log entries.

All filter parameters are optional and combinable.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .audit_log import AuditLog, AuditEntry, ChainVerifyResult


class AuditQuery:
    """
    Read-side query interface for the AuditLog.

    Usage:
        q = AuditQuery(log)
        entries = q.query(op="entity_create", actor="pipeline", limit=50)
    """

    def __init__(self, log: AuditLog) -> None:
        self._log = log

    def query(
        self,
        *,
        from_dt: Optional[datetime] = None,
        to_dt: Optional[datetime] = None,
        actor: Optional[str] = None,
        op: Optional[str] = None,
        workspace: Optional[str] = None,
        target_urn: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEntry]:
        """
        Return filtered, paginated audit entries.

        Date filters compare against the ISO-8601 timestamp_utc field.
        All other filters are exact-match strings.
        """
        entries = self._log.all()

        filtered = []
        for e in entries:
            # Date range filter
            if from_dt is not None:
                ts = _parse_ts(e.timestamp_utc)
                if ts is None or ts < from_dt:
                    continue
            if to_dt is not None:
                ts = _parse_ts(e.timestamp_utc)
                if ts is None or ts > to_dt:
                    continue

            # Exact-match filters
            if actor is not None and e.actor != actor:
                continue
            if op is not None and e.op != op:
                continue
            if workspace is not None and e.workspace != workspace:
                continue
            if target_urn is not None and e.target_urn != target_urn:
                continue

            filtered.append(e)

        # Pagination
        return filtered[offset : offset + limit]

    def count(
        self,
        *,
        op: Optional[str] = None,
        actor: Optional[str] = None,
        workspace: Optional[str] = None,
    ) -> int:
        """Count matching entries without pagination."""
        return len(self.query(op=op, actor=actor, workspace=workspace, limit=10_000))

    def verify(self) -> ChainVerifyResult:
        """Delegate chain verification to the underlying log."""
        return self._log.verify_chain()


# ── Helpers ──────────────────────────────────────────────────────────────────

def _parse_ts(ts_str: str) -> Optional[datetime]:
    """Parse ISO-8601 string; return None on failure."""
    try:
        # Python 3.11+ fromisoformat handles Z; older needs replace
        return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
