"""
ADR-0082 — DriftStore: file-based persistence for DriftItems and DriftSnapshots.

Layout under .brain/drift/:
    items/
        <item_id>.json          — one file per DriftItem (upsert-by-id)
    snapshots/
        <snapshot_id>.json      — append-only, one per snapshot run
    latest.json                 — symlink or copy of the most recent snapshot id

All I/O is synchronous (pathlib); the scheduler calls this from a background
thread / asyncio executor. The REST layer wraps calls in run_in_executor.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import structlog

from companybrain.drift.models import DriftItem, DriftSnapshot

log = structlog.get_logger(__name__)


class DriftStore:
    """
    Thin persistence layer for drift entities.

    Parameters
    ----------
    brain_root:
        Path to the .brain/ directory of the target repository.
        Defaults to env var BRAIN_DEMO_REPO_PATH / .brain.
    """

    def __init__(self, brain_root: Optional[Path] = None):
        if brain_root is None:
            repo = (
                os.environ.get("BRAIN_DEMO_REPO_PATH")
                or os.environ.get("TARGET_REPO")
                or os.environ.get("BRAIN_REPO_PATH")
                or "/tmp/brain-drift-default"
            )
            brain_root = Path(repo) / ".brain"
        self._root = Path(brain_root)
        self._items_dir = self._root / "drift" / "items"
        self._snapshots_dir = self._root / "drift" / "snapshots"
        self._latest_path = self._root / "drift" / "latest.json"

    # ── Items ──────────────────────────────────────────────────────────────────

    def upsert_item(self, item: DriftItem) -> DriftItem:
        """
        Persist a DriftItem. Creates if new, overwrites if existing.
        Returns the item (allows chaining).
        """
        self._items_dir.mkdir(parents=True, exist_ok=True)
        path = self._items_dir / f"{item.id}.json"
        path.write_text(json.dumps(item.to_dict(), indent=2, sort_keys=True))
        log.debug("drift.store.upsert_item", item_id=item.id, state=item.state)
        return item

    def load_item(self, item_id: str) -> Optional[DriftItem]:
        """Load a single DriftItem by ID. Returns None if not found."""
        path = self._items_dir / f"{item_id}.json"
        if not path.exists():
            return None
        try:
            return DriftItem.from_dict(json.loads(path.read_text()))
        except Exception as exc:
            log.warning("drift.store.load_item.error", item_id=item_id, error=str(exc))
            return None

    def load_all_items(self) -> list[DriftItem]:
        """Load every DriftItem in the store. Returns empty list if none."""
        if not self._items_dir.exists():
            return []
        items: list[DriftItem] = []
        for path in sorted(self._items_dir.glob("*.json")):
            try:
                items.append(DriftItem.from_dict(json.loads(path.read_text())))
            except Exception as exc:
                log.warning("drift.store.load_all.skip", path=str(path), error=str(exc))
        return items

    def find_or_create_item(
        self,
        rule_id: str,
        scope_urn: str,
        kind: str,
        *,
        defaults: dict,
    ) -> tuple[DriftItem, bool]:
        """
        Find an existing item by its stable ID, or create one from *defaults*.

        Returns ``(item, created)`` — created=True means it's brand new.
        """
        item_id = DriftItem.make_id(rule_id, scope_urn, kind)
        existing = self.load_item(item_id)
        if existing is not None:
            return existing, False

        item = DriftItem(
            id=item_id,
            rule_id=rule_id,
            scope_urn=scope_urn,
            kind=kind,  # type: ignore[arg-type]
            **defaults,
        )
        self.upsert_item(item)
        return item, True

    # ── Snapshots ─────────────────────────────────────────────────────────────

    def save_snapshot(self, snapshot: DriftSnapshot) -> None:
        """Persist a snapshot (append-only) and update the latest pointer."""
        self._snapshots_dir.mkdir(parents=True, exist_ok=True)
        path = self._snapshots_dir / f"{snapshot.snapshot_id}.json"
        path.write_text(json.dumps(snapshot.to_dict(), indent=2, sort_keys=True))
        # Update latest pointer.
        self._latest_path.write_text(json.dumps({"snapshot_id": snapshot.snapshot_id}))
        log.info(
            "drift.store.snapshot_saved",
            snapshot_id=snapshot.snapshot_id,
            items_open=snapshot.items_open,
            new=len(snapshot.new_since_last),
            resolved=len(snapshot.resolved_since_last),
        )

    def load_snapshot(self, snapshot_id: str) -> Optional[DriftSnapshot]:
        """Load a snapshot by ID. Returns None if not found."""
        path = self._snapshots_dir / f"{snapshot_id}.json"
        if not path.exists():
            return None
        try:
            return DriftSnapshot.from_dict(json.loads(path.read_text()))
        except Exception as exc:
            log.warning("drift.store.load_snapshot.error", snapshot_id=snapshot_id, error=str(exc))
            return None

    def load_latest_snapshot(self) -> Optional[DriftSnapshot]:
        """Load the most recently saved snapshot. Returns None if no snapshot exists."""
        if not self._latest_path.exists():
            return None
        try:
            data = json.loads(self._latest_path.read_text())
            return self.load_snapshot(data["snapshot_id"])
        except Exception as exc:
            log.warning("drift.store.load_latest.error", error=str(exc))
            return None

    def list_snapshot_ids(self) -> list[str]:
        """Return all snapshot IDs in chronological order (by filename = id = uuid)."""
        if not self._snapshots_dir.exists():
            return []
        return sorted(
            p.stem for p in self._snapshots_dir.glob("*.json")
        )

    # ── Filtered item queries ──────────────────────────────────────────────────

    def query_items(
        self,
        *,
        state: Optional[str] = None,
        severity: Optional[str] = None,
        domain: Optional[str] = None,
    ) -> list[DriftItem]:
        """Filter items by optional state, severity, and/or domain membership."""
        items = self.load_all_items()
        if state:
            items = [i for i in items if i.state == state]
        if severity:
            items = [i for i in items if i.severity == severity]
        if domain:
            items = [i for i in items if domain in i.domain_areas]
        return items
