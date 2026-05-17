"""
TTL evaluator — ADR-0064 M2.

Sweeps all entities and applies expiry actions based on their ttl_class.

Design for testability:
  - `now` is injectable (default: datetime.utcnow()) for time-travel tests.
  - `write_fn` and `audit_fn` are injected; no hard dependency on real stores.
  - The sweep is synchronous; suitable for a daily cron job.

Expiry actions:
  | ttl_class             | Action               |
  |-----------------------|----------------------|
  | permanent             | none                 |
  | business_indefinite   | tombstone            |
  | operational           | tombstone            |
  | transient             | hard-delete          |
  | pii_unless_consented  | redact PII spans     |
  | secret                | reject at ingest (not handled here; done in scan step) |
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from .ttl_classifier import TTLClass, expiry_days
from .pii_detector import PIIFinding


# ── Result types ─────────────────────────────────────────────────────────────

@dataclass
class SweepResult:
    tombstoned: int = 0
    deleted: int = 0
    redacted: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)


# ── Redaction ─────────────────────────────────────────────────────────────────

def redact_pii(text: str, findings: list[PIIFinding]) -> str:
    """
    Replace PII spans in `text` with `[REDACTED:kind]` placeholders.
    Spans are applied in reverse order (highest offset first) so earlier
    offsets are not shifted by the replacement.
    """
    # Sort findings by span start descending
    sorted_findings = sorted(findings, key=lambda f: f.span[0], reverse=True)
    result = text
    for finding in sorted_findings:
        start, end = finding.span
        placeholder = f"[REDACTED:{finding.kind}]"
        result = result[:start] + placeholder + result[end:]
    return result


# ── Sweep ─────────────────────────────────────────────────────────────────────

def sweep(
    entities: list[dict],
    *,
    now: Optional[datetime] = None,
    write_fn: Optional[Callable[[dict], None]] = None,
    delete_fn: Optional[Callable[[str], None]] = None,
    audit_fn: Optional[Callable[[str, dict], None]] = None,
) -> SweepResult:
    """
    Sweep a list of entity dicts and apply TTL expiry actions.

    Args:
        entities: List of entity dicts.  Each must have:
            - 'id': str — entity identifier
            - 'ttl_class': str — TTLClass value
            - 'created_at': str — ISO-8601 creation timestamp
            - 'content' (optional): str — for redaction
            - 'pii_findings' (optional): list[dict] — for redaction
        now: Reference time for expiry calculation.  Defaults to UTC now.
            Pass a fixed datetime to time-travel in tests.
        write_fn: Called with updated entity dict after redaction.
        delete_fn: Called with entity id for hard-delete.
        audit_fn: Called with (op, payload) for each action.

    Returns:
        SweepResult summarising what was done.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    result = SweepResult()

    for entity in entities:
        try:
            _process_entity(entity, now=now, result=result,
                           write_fn=write_fn, delete_fn=delete_fn,
                           audit_fn=audit_fn)
        except Exception as exc:
            result.errors.append(f"entity {entity.get('id', '?')}: {exc}")

    return result


def _process_entity(
    entity: dict,
    *,
    now: datetime,
    result: SweepResult,
    write_fn: Optional[Callable],
    delete_fn: Optional[Callable],
    audit_fn: Optional[Callable],
) -> None:
    ttl_class_str = entity.get("ttl_class")
    if not ttl_class_str:
        result.skipped += 1
        return

    try:
        ttl_class = TTLClass(ttl_class_str)
    except ValueError:
        result.skipped += 1
        return

    # permanent → no action ever
    if ttl_class == TTLClass.PERMANENT:
        result.skipped += 1
        return

    # Compute expiry date from created_at
    created_at_str = entity.get("created_at") or entity.get("last_updated", "")
    if not created_at_str:
        result.skipped += 1
        return

    created_at = _parse_dt(created_at_str)
    if created_at is None:
        result.skipped += 1
        return

    days = expiry_days(ttl_class)
    if days is None:
        result.skipped += 1
        return

    expiry_dt = created_at + timedelta(days=days)
    # Ensure both are timezone-aware for comparison
    if expiry_dt.tzinfo is None:
        expiry_dt = expiry_dt.replace(tzinfo=timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if now < expiry_dt:
        result.skipped += 1
        return

    # Entity is expired — apply action
    entity_id = entity.get("id", "unknown")

    if ttl_class == TTLClass.PII_UNLESS_CONSENTED:
        _apply_redact(entity, entity_id, result, write_fn=write_fn, audit_fn=audit_fn)

    elif ttl_class == TTLClass.TRANSIENT:
        _apply_delete(entity_id, result, delete_fn=delete_fn, audit_fn=audit_fn)

    elif ttl_class in (TTLClass.OPERATIONAL, TTLClass.BUSINESS_INDEFINITE):
        _apply_tombstone(entity, entity_id, result, write_fn=write_fn, audit_fn=audit_fn)


def _apply_redact(
    entity: dict,
    entity_id: str,
    result: SweepResult,
    *,
    write_fn: Optional[Callable],
    audit_fn: Optional[Callable],
) -> None:
    """Replace PII spans with [REDACTED:kind] placeholders."""
    pii_raw = entity.get("pii_findings", [])
    findings = [
        PIIFinding(
            kind=f.get("kind", "unknown"),
            text=f.get("text", ""),
            span=tuple(f.get("span", [0, 0])),
            confidence=f.get("confidence", 0.0),
            detector=f.get("detector", "regex"),
        )
        for f in pii_raw
        if isinstance(f, dict)
    ]

    content = entity.get("content") or entity.get("t1_summary", "")
    if content and findings:
        redacted = redact_pii(content, findings)
        entity = dict(entity)
        if "content" in entity:
            entity["content"] = redacted
        if "t1_summary" in entity:
            entity["t1_summary"] = redacted
        entity["pii_scrubbed"] = True

    if write_fn:
        write_fn(entity)

    if audit_fn:
        audit_fn("pii_redact", {
            "entity_id": entity_id,
            "ttl_class": entity.get("ttl_class"),
            "pii_kinds": [f.kind for f in findings],
        })

    result.redacted += 1


def _apply_delete(
    entity_id: str,
    result: SweepResult,
    *,
    delete_fn: Optional[Callable],
    audit_fn: Optional[Callable],
) -> None:
    if delete_fn:
        delete_fn(entity_id)

    if audit_fn:
        audit_fn("ttl_expire", {"entity_id": entity_id, "action": "hard_delete"})

    result.deleted += 1


def _apply_tombstone(
    entity: dict,
    entity_id: str,
    result: SweepResult,
    *,
    write_fn: Optional[Callable],
    audit_fn: Optional[Callable],
) -> None:
    entity = dict(entity)
    entity["tombstoned"] = True
    entity["tombstoned_at"] = datetime.now(timezone.utc).isoformat()

    if write_fn:
        write_fn(entity)

    if audit_fn:
        audit_fn("ttl_expire", {"entity_id": entity_id, "action": "tombstone"})

    result.tombstoned += 1


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_dt(ts: str) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, AttributeError):
        return None
