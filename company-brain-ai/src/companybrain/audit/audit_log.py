"""
Hash-chained audit log — ADR-0064 M3.

Each AuditEntry carries:
  - prev_hash: the self_hash of the immediately preceding entry (genesis = "0"*64)
  - self_hash: sha256 of the canonical JSON of this entry (minus self_hash field)

Tampering with any past entry breaks the chain from that point forward.
verify_chain() walks forward and reports the first inconsistency.

Storage: append-only JSONL file.  One JSON object per line.
"""
from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional


# ── Data model ────────────────────────────────────────────────────────────────

AuditOp = Literal[
    "entity_create", "entity_update", "entity_delete",
    "edge_create", "edge_delete",
    "ingest_chunk", "pii_redact", "ttl_expire", "ttl_reject",
    "query", "audit_export",
]

GENESIS_HASH = "0" * 64


@dataclass
class AuditEntry:
    seq: int
    timestamp_utc: str              # ISO-8601 UTC
    actor: str                      # service-account or user_id
    workspace: str
    op: str                         # AuditOp value
    target_urn: Optional[str]
    diff: Optional[dict]            # before/after snippet
    rationale: Optional[str]
    prev_hash: str
    self_hash: str = ""             # computed on append; empty until then

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AuditEntry":
        valid = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in data.items() if k in valid})


@dataclass
class ChainVerifyResult:
    is_valid: bool
    entry_count: int
    first_bad_seq: Optional[int] = None
    error: Optional[str] = None


# ── Hash helpers ─────────────────────────────────────────────────────────────

def _canonical_json(entry: AuditEntry) -> str:
    """
    Produce a deterministic JSON string for hashing.
    self_hash is excluded; all other fields sorted alphabetically.
    """
    d = entry.to_dict()
    d.pop("self_hash", None)
    return json.dumps(d, sort_keys=True, ensure_ascii=True, default=str)


def _compute_self_hash(entry: AuditEntry) -> str:
    canonical = _canonical_json(entry)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ── AuditLog ─────────────────────────────────────────────────────────────────

class AuditLog:
    """
    Append-only hash-chained audit log backed by a JSONL file.

    Thread-safe: a per-instance lock serialises all appends.
    """

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._seq: int = 0
        self._last_hash: str = GENESIS_HASH

        # Bootstrap sequence counter from existing file.
        if self._path.exists():
            entries = self._load_all()
            if entries:
                self._seq = entries[-1].seq
                self._last_hash = entries[-1].self_hash

    # ── Public API ───────────────────────────────────────────────────────────

    def append(
        self,
        *,
        actor: str,
        workspace: str,
        op: str,
        target_urn: Optional[str] = None,
        diff: Optional[dict] = None,
        rationale: Optional[str] = None,
        timestamp_utc: Optional[datetime] = None,
    ) -> AuditEntry:
        """
        Append a new entry to the log.  Computes the hash chain and writes
        atomically under a lock.  Returns the completed AuditEntry.
        """
        with self._lock:
            self._seq += 1
            ts = (timestamp_utc or datetime.now(timezone.utc)).isoformat()

            entry = AuditEntry(
                seq=self._seq,
                timestamp_utc=ts,
                actor=actor,
                workspace=workspace,
                op=op,
                target_urn=target_urn,
                diff=diff,
                rationale=rationale,
                prev_hash=self._last_hash,
                self_hash="",  # computed below
            )

            entry.self_hash = _compute_self_hash(entry)
            self._last_hash = entry.self_hash

            with self._path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry.to_dict(), default=str) + "\n")
                fh.flush()

            return entry

    def verify_chain(self) -> ChainVerifyResult:
        """
        Walk all entries from seq=1 forward.
        Re-compute each self_hash and confirm prev_hash chains correctly.
        """
        entries = self._load_all()
        if not entries:
            return ChainVerifyResult(is_valid=True, entry_count=0)

        expected_prev = GENESIS_HASH

        for entry in entries:
            # Check prev_hash linkage
            if entry.prev_hash != expected_prev:
                return ChainVerifyResult(
                    is_valid=False,
                    entry_count=len(entries),
                    first_bad_seq=entry.seq,
                    error=f"prev_hash mismatch at seq={entry.seq}",
                )

            # Re-compute self_hash
            stored_hash = entry.self_hash
            recomputed = _compute_self_hash(entry)
            if stored_hash != recomputed:
                return ChainVerifyResult(
                    is_valid=False,
                    entry_count=len(entries),
                    first_bad_seq=entry.seq,
                    error=f"self_hash mismatch at seq={entry.seq}",
                )

            expected_prev = stored_hash

        return ChainVerifyResult(is_valid=True, entry_count=len(entries))

    def read_range(
        self,
        from_seq: Optional[int] = None,
        to_seq: Optional[int] = None,
    ) -> list[AuditEntry]:
        """Return entries where from_seq <= seq <= to_seq (inclusive)."""
        entries = self._load_all()
        result = []
        for e in entries:
            if from_seq is not None and e.seq < from_seq:
                continue
            if to_seq is not None and e.seq > to_seq:
                break
            result.append(e)
        return result

    def count(self) -> int:
        """Return the number of entries in the log."""
        with self._lock:
            return self._seq

    def all(self) -> list[AuditEntry]:
        return self._load_all()

    # ── Private ──────────────────────────────────────────────────────────────

    def _load_all(self) -> list[AuditEntry]:
        if not self._path.exists():
            return []
        entries = []
        with self._path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    data = json.loads(line)
                    entries.append(AuditEntry.from_dict(data))
                except (json.JSONDecodeError, TypeError):
                    continue
        return entries
