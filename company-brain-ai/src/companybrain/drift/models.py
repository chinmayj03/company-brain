"""
ADR-0082 M1 — DriftItem + DriftSnapshot entity schemas.

These are the canonical typed shapes for all drift data in company-brain.
Persistence is via DriftStore (store.py); the models themselves are pure
dataclasses with no I/O.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal, Optional


# ── Type aliases ──────────────────────────────────────────────────────────────

RuleSource = Literal["adr", "spec", "convention", "dependency_policy", "schema_pact"]
DriftKind = Literal[
    "structural",
    "schema",
    "convention",
    "dependency_policy",
    "data_flow",
    "ownership",
]
Severity = Literal["low", "medium", "high", "critical"]
DriftState = Literal["open", "acknowledged", "in_flight", "resolved", "waived"]


# ── Sub-records ───────────────────────────────────────────────────────────────

@dataclass
class ResolutionRecord:
    """Attached to a DriftItem when it leaves active violation status."""
    resolved_at: datetime
    resolved_by: Optional[str] = None          # "auto" or human actor
    auto_resolved: bool = False
    justification: Optional[str] = None        # required for waived
    pr_url: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "resolved_at": self.resolved_at.isoformat(),
            "resolved_by": self.resolved_by,
            "auto_resolved": self.auto_resolved,
            "justification": self.justification,
            "pr_url": self.pr_url,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "ResolutionRecord":
        return cls(
            resolved_at=_parse_dt(d["resolved_at"]),
            resolved_by=d.get("resolved_by"),
            auto_resolved=d.get("auto_resolved", False),
            justification=d.get("justification"),
            pr_url=d.get("pr_url"),
        )


# ── Core entity ───────────────────────────────────────────────────────────────

@dataclass
class DriftItem:
    """
    A single persistent drift violation.

    ``id`` is a stable 16-char hex hash of (rule_id, scope_urn, kind) so the
    same violation always maps to the same item across snapshot runs.
    """
    id: str
    rule_id: str
    rule_source: RuleSource
    kind: DriftKind
    scope_urn: str
    domain_areas: list[str]
    severity: Severity
    detected_at: datetime
    last_seen_at: datetime
    age_days: float
    state: DriftState
    resolution: Optional[ResolutionRecord] = None
    related_prs: list[str] = field(default_factory=list)
    estimated_remediation_days: Optional[float] = None
    description: str = ""
    first_violated_commit: Optional[str] = None
    waive_expires_at: Optional[datetime] = None   # auto-reactivation for waived items

    # ── Stable ID ──────────────────────────────────────────────────────────

    @staticmethod
    def make_id(rule_id: str, scope_urn: str, kind: str) -> str:
        """Deterministic 16-char hex ID. Identical inputs always produce the same ID."""
        raw = f"{rule_id}:{scope_urn}:{kind}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    # ── Serialization ──────────────────────────────────────────────────────

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "rule_id": self.rule_id,
            "rule_source": self.rule_source,
            "kind": self.kind,
            "scope_urn": self.scope_urn,
            "domain_areas": self.domain_areas,
            "severity": self.severity,
            "detected_at": self.detected_at.isoformat(),
            "last_seen_at": self.last_seen_at.isoformat(),
            "age_days": self.age_days,
            "state": self.state,
            "resolution": self.resolution.to_dict() if self.resolution else None,
            "related_prs": self.related_prs,
            "estimated_remediation_days": self.estimated_remediation_days,
            "description": self.description,
            "first_violated_commit": self.first_violated_commit,
            "waive_expires_at": self.waive_expires_at.isoformat() if self.waive_expires_at else None,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DriftItem":
        resolution = None
        if d.get("resolution"):
            resolution = ResolutionRecord.from_dict(d["resolution"])
        waive_expires_at = None
        if d.get("waive_expires_at"):
            waive_expires_at = _parse_dt(d["waive_expires_at"])
        return cls(
            id=d["id"],
            rule_id=d["rule_id"],
            rule_source=d["rule_source"],
            kind=d["kind"],
            scope_urn=d["scope_urn"],
            domain_areas=d.get("domain_areas", []),
            severity=d["severity"],
            detected_at=_parse_dt(d["detected_at"]),
            last_seen_at=_parse_dt(d["last_seen_at"]),
            age_days=d.get("age_days", 0.0),
            state=d["state"],
            resolution=resolution,
            related_prs=d.get("related_prs", []),
            estimated_remediation_days=d.get("estimated_remediation_days"),
            description=d.get("description", ""),
            first_violated_commit=d.get("first_violated_commit"),
            waive_expires_at=waive_expires_at,
        )

    def refresh_age(self, as_of: Optional[datetime] = None) -> None:
        """Recompute age_days in-place from detected_at → as_of (or now)."""
        now = as_of or datetime.now(timezone.utc)
        detected = self.detected_at if self.detected_at.tzinfo else self.detected_at.replace(tzinfo=timezone.utc)
        self.age_days = (now - detected).total_seconds() / 86400


# ── Aggregate snapshot ────────────────────────────────────────────────────────

@dataclass
class DriftSnapshot:
    """
    Periodic aggregate of all drift items at a point in time.

    Produced by the snapshot scheduler (scheduler.py) and persisted
    append-only under .brain/drift/snapshots/.
    """
    snapshot_id: str
    snapshot_at: datetime
    workspace: str
    items_open: int
    items_acknowledged: int
    items_in_flight: int
    items_by_domain: dict[str, int]
    items_by_severity: dict[str, int]
    new_since_last: list[str]        # item IDs first seen in this snapshot
    resolved_since_last: list[str]   # item IDs resolved since last snapshot
    delta_score: float               # positive = more drift, negative = improving

    # Full item list stored separately in items/; snapshot carries IDs only for
    # compact serialization. Use DriftStore.load_items_for_snapshot() to hydrate.
    all_item_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "snapshot_at": self.snapshot_at.isoformat(),
            "workspace": self.workspace,
            "items_open": self.items_open,
            "items_acknowledged": self.items_acknowledged,
            "items_in_flight": self.items_in_flight,
            "items_by_domain": self.items_by_domain,
            "items_by_severity": self.items_by_severity,
            "new_since_last": self.new_since_last,
            "resolved_since_last": self.resolved_since_last,
            "delta_score": self.delta_score,
            "all_item_ids": self.all_item_ids,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "DriftSnapshot":
        return cls(
            snapshot_id=d["snapshot_id"],
            snapshot_at=_parse_dt(d["snapshot_at"]),
            workspace=d["workspace"],
            items_open=d.get("items_open", 0),
            items_acknowledged=d.get("items_acknowledged", 0),
            items_in_flight=d.get("items_in_flight", 0),
            items_by_domain=d.get("items_by_domain", {}),
            items_by_severity=d.get("items_by_severity", {}),
            new_since_last=d.get("new_since_last", []),
            resolved_since_last=d.get("resolved_since_last", []),
            delta_score=d.get("delta_score", 0.0),
            all_item_ids=d.get("all_item_ids", []),
        )


# ── Scoring output ────────────────────────────────────────────────────────────

@dataclass
class DomainDriftScore:
    """Per-domain weighted drift score returned by scorer.domain_drift_score()."""
    domain: str
    item_count: int
    weighted_score: float
    critical_count: int
    oldest_item_age_days: float
    in_flight_remediation_days: float = 0.0

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "item_count": self.item_count,
            "weighted_score": round(self.weighted_score, 3),
            "critical_count": self.critical_count,
            "oldest_item_age_days": round(self.oldest_item_age_days, 1),
            "in_flight_remediation_days": round(self.in_flight_remediation_days, 1),
        }


# ── Helpers ───────────────────────────────────────────────────────────────────

def _parse_dt(s: str) -> datetime:
    """Parse ISO-8601 datetime string, always returning a timezone-aware datetime."""
    # Python 3.11+ supports datetime.fromisoformat with Z suffix natively;
    # for 3.10 compatibility we normalise the suffix first.
    s = s.replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt
