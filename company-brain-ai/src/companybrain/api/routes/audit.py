"""
Audit query API — ADR-0064 M3.

GET /audit — returns filtered, paginated audit log entries.

Query parameters:
    from_dt:   ISO-8601 UTC datetime — only entries after this time
    to_dt:     ISO-8601 UTC datetime — only entries before this time
    actor:     exact match on entry.actor
    op:        exact match on entry.op
    workspace: exact match on entry.workspace
    target_urn: exact match on entry.target_urn
    limit:     max entries per page (default 100, max 1000)
    offset:    pagination offset (default 0)

Response:
    {
        "entries": [...],
        "chain_verified": bool,
        "total": int,   // total matching before pagination
        "limit": int,
        "offset": int
    }
"""
from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from companybrain.audit import AuditLog, AuditQuery

router = APIRouter(prefix="/audit", tags=["audit"])

_log: Optional[AuditLog] = None


def _get_log() -> AuditLog:
    global _log
    if _log is None:
        audit_path = os.environ.get("AUDIT_LOG_PATH", "./audit/audit.jsonl")
        _log = AuditLog(path=Path(audit_path))
    return _log


def set_audit_log(log: AuditLog) -> None:
    """Override the audit log instance (useful in tests)."""
    global _log
    _log = log


@router.get("")
def query_audit(
    from_dt: Optional[str] = Query(None, description="ISO-8601 UTC start datetime"),
    to_dt: Optional[str] = Query(None, description="ISO-8601 UTC end datetime"),
    actor: Optional[str] = Query(None),
    op: Optional[str] = Query(None),
    workspace: Optional[str] = Query(None),
    target_urn: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> dict:
    """
    Query the audit log with optional filters.
    Returns paginated entries plus a chain_verified flag.
    """
    log = _get_log()
    q = AuditQuery(log)

    # Parse datetime params
    from_parsed: Optional[datetime] = None
    to_parsed: Optional[datetime] = None

    if from_dt:
        try:
            from_parsed = datetime.fromisoformat(from_dt.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid from_dt: {from_dt!r}")

    if to_dt:
        try:
            to_parsed = datetime.fromisoformat(to_dt.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=422, detail=f"Invalid to_dt: {to_dt!r}")

    # Get all matching (for total count), then paginate
    all_matching = q.query(
        from_dt=from_parsed,
        to_dt=to_parsed,
        actor=actor,
        op=op,
        workspace=workspace,
        target_urn=target_urn,
        limit=10_000,
        offset=0,
    )
    total = len(all_matching)
    page = all_matching[offset: offset + limit]

    # Chain verification
    chain_result = log.verify_chain()

    return {
        "entries": [e.to_dict() for e in page],
        "chain_verified": chain_result.is_valid,
        "chain_entry_count": chain_result.entry_count,
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/verify")
def verify_chain() -> dict:
    """Verify the hash chain integrity of the entire audit log."""
    log = _get_log()
    result = log.verify_chain()
    return {
        "is_valid": result.is_valid,
        "entry_count": result.entry_count,
        "first_bad_seq": result.first_bad_seq,
        "error": result.error,
    }
