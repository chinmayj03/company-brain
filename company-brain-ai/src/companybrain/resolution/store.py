"""
ResolutionStore — JSON-backed persistence for entity resolution decisions.

Layout (relative to the store root):
    resolution/
        index.json          — artifact_urn → domain_urn
        matches.json        — list of persisted ResolutionMatch dicts
        confirmations.json  — list of human confirmation records

The store is synchronous by design (JSON file I/O is fast, lock-protected,
and the resolution path is not on the hot query path).

Thread safety: a threading.Lock guards every write.  Concurrent reads of
an already-loaded snapshot are safe because the in-memory dict is replaced
atomically via assignment.
"""
from __future__ import annotations

import json
import threading
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Optional

import structlog

from companybrain.resolution.models import (
    EntityCandidate,
    ResolutionMatch,
    ResolutionTier,
)

log = structlog.get_logger(__name__)


class ResolutionStore:
    """
    Persists and retrieves entity resolution decisions.

    Parameters
    ----------
    store_root:
        Directory where resolution JSON files are written.  Created on first use.
    """

    def __init__(self, store_root: Path) -> None:
        self._root = Path(store_root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        self._index_path        = self._root / "index.json"
        self._matches_path      = self._root / "matches.json"
        self._confirmations_path = self._root / "confirmations.json"

    # ── Reads ─────────────────────────────────────────────────────────────────

    def get_domain_entity(self, artifact_urn: str) -> Optional[str]:
        """
        Return the domain URN that *artifact_urn* has been resolved to, or None.
        """
        index = self._load_index()
        return index.get(artifact_urn)

    def get_pending_matches(self) -> list[dict]:
        """Return all matches with status == 'pending' (awaiting human confirmation)."""
        return [m for m in self._load_matches() if m.get("status") == "pending"]

    def get_artifacts_for_entity(self, domain_urn: str) -> list[str]:
        """Return all artifact URNs resolved to *domain_urn*."""
        index = self._load_index()
        return [urn for urn, d in index.items() if d == domain_urn]

    def get_match_by_id(self, match_id: str) -> Optional[dict]:
        """Return a match record by its deterministic id, or None."""
        for m in self._load_matches():
            if m.get("id") == match_id:
                return m
        return None

    # ── Writes ────────────────────────────────────────────────────────────────

    def record_resolution(self, match: ResolutionMatch) -> None:
        """
        Persist a ResolutionMatch and update the artifact → domain index.

        If the same match id already exists the existing record is replaced
        (idempotent).
        """
        with self._lock:
            # Update index for both artifacts
            index = self._load_index()
            index[match.candidate_a.artifact_urn] = match.domain_urn
            index[match.candidate_b.artifact_urn] = match.domain_urn
            self._save_index(index)

            # Upsert match record
            matches = self._load_matches()
            matches = [m for m in matches if m.get("id") != match.id]
            matches.append(self._match_to_dict(match))
            self._save_matches(matches)

        log.debug(
            "resolution.recorded",
            match_id=match.id,
            tier=match.tier.value,
            domain_urn=match.domain_urn,
        )

    def record_human_confirmation(
        self,
        artifact_urn: str,
        domain_urn: str,
        match_id: Optional[str] = None,
    ) -> None:
        """
        Record a human operator's confirmation that *artifact_urn* resolves to
        *domain_urn*.  Updates the index and marks the pending match (if any)
        as confirmed.
        """
        with self._lock:
            # Update index
            index = self._load_index()
            index[artifact_urn] = domain_urn
            self._save_index(index)

            # Mark match as confirmed
            if match_id:
                matches = self._load_matches()
                updated = []
                for m in matches:
                    if m.get("id") == match_id:
                        m = dict(m)
                        m["status"] = "confirmed"
                    updated.append(m)
                self._save_matches(updated)

            # Append confirmation record
            confirmations = self._load_confirmations()
            confirmations.append({
                "artifact_urn": artifact_urn,
                "domain_urn":   domain_urn,
                "match_id":     match_id,
                "confirmed_at": datetime.utcnow().isoformat() + "Z",
            })
            self._save_confirmations(confirmations)

        log.info(
            "resolution.human_confirmed",
            artifact_urn=artifact_urn,
            domain_urn=domain_urn,
        )

    def record_human_rejection(self, match_id: str) -> None:
        """Mark a pending match as rejected."""
        with self._lock:
            matches = self._load_matches()
            updated = []
            for m in matches:
                if m.get("id") == match_id:
                    m = dict(m)
                    m["status"] = "rejected"
                updated.append(m)
            self._save_matches(updated)

        log.info("resolution.human_rejected", match_id=match_id)

    # ── Internal I/O helpers ─────────────────────────────────────────────────

    def _load_index(self) -> dict[str, str]:
        return self._load_json(self._index_path, default={})

    def _save_index(self, data: dict) -> None:
        self._save_json(self._index_path, data)

    def _load_matches(self) -> list[dict]:
        return self._load_json(self._matches_path, default=[])

    def _save_matches(self, data: list) -> None:
        self._save_json(self._matches_path, data)

    def _load_confirmations(self) -> list[dict]:
        return self._load_json(self._confirmations_path, default=[])

    def _save_confirmations(self, data: list) -> None:
        self._save_json(self._confirmations_path, data)

    @staticmethod
    def _load_json(path: Path, *, default):
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return default

    @staticmethod
    def _save_json(path: Path, data) -> None:
        path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    @staticmethod
    def _match_to_dict(match: ResolutionMatch) -> dict:
        """Serialize a ResolutionMatch to a plain dict for JSON storage."""

        def candidate_to_dict(c: EntityCandidate) -> dict:
            return {
                "artifact_urn":       c.artifact_urn,
                "source_type":        c.source_type,
                "title":              c.title,
                "content_snippet":    c.content_snippet,
                "domain_hints":       c.domain_hints,
                "explicit_domain_urn": c.explicit_domain_urn,
            }

        return {
            "id":          match.id,
            "candidate_a": candidate_to_dict(match.candidate_a),
            "candidate_b": candidate_to_dict(match.candidate_b),
            "tier":        match.tier.value,
            "confidence":  match.confidence,
            "domain_urn":  match.domain_urn,
            "status":      match.status,
        }
