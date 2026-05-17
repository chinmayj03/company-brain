"""
Few-shot bank: persist and evict Q&A exemplars per workspace per persona.

Storage layout:
  {storage_path}/{workspace_id}/{persona}.json

Each file is a JSON array of serialised FewShotExample objects.
In-memory state is the authoritative write buffer; the file is the durable store.
Eviction rewrites the file atomically (write-then-rename).
"""
from __future__ import annotations

import json
import math
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import structlog

log = structlog.get_logger(__name__)


@dataclass
class FewShotExample:
    id: str                    # uuid
    workspace_id: str
    persona: str               # "developer" | "pm" | "vp_eng" | "generic"
    question: str
    answer: str
    citations: List[str]       # entity URNs cited
    quality_score: float       # 0.0-1.0; thumbs_up=1.0, no_feedback=0.5, thumbs_down=0.0
    embedding: List[float]     # sentence embedding of question (for similarity search)
    created_at: datetime
    last_used_at: datetime
    use_count: int = 0


# ── Serialisation helpers ─────────────────────────────────────────────────────

def _to_dict(ex: FewShotExample) -> dict:
    d = asdict(ex)
    d["created_at"]  = ex.created_at.isoformat()
    d["last_used_at"] = ex.last_used_at.isoformat()
    return d


def _from_dict(d: dict) -> FewShotExample:
    d = dict(d)
    d["created_at"]  = datetime.fromisoformat(d["created_at"])
    d["last_used_at"] = datetime.fromisoformat(d["last_used_at"])
    return FewShotExample(**d)


# ── Eviction scoring ──────────────────────────────────────────────────────────

def _eviction_key(ex: FewShotExample, now_ts: float) -> float:
    """
    Lower score → evicted first.

    Combines quality_score (0..1) with recency_factor (0..1).
    recency_factor decays to 0.5 over 30 days, so a fresh low-quality entry
    still beats a stale medium-quality one after ~60 days of no use.
    """
    last_used_ts = ex.last_used_at.timestamp()
    age_days = max(0.0, (now_ts - last_used_ts) / 86_400)
    recency_factor = math.exp(-age_days / 30.0)  # half-life ~30 days
    return ex.quality_score * recency_factor


# ── Bank ──────────────────────────────────────────────────────────────────────

class FewShotBank:
    """
    Persist successful Q&A pairs per workspace per persona.

    Max ``max_per_bucket`` examples per (workspace, persona) bucket.
    LRU eviction weighted by quality_score × recency_factor:
    evict lowest score first.
    """

    def __init__(self, storage_path: Path, max_per_bucket: int = 200) -> None:
        self._root = Path(storage_path)
        self._max  = max_per_bucket
        # In-memory cache: (workspace_id, persona) → list[FewShotExample]
        self._cache: Dict[Tuple[str, str], List[FewShotExample]] = {}

    # ── Public API ────────────────────────────────────────────────────────────

    def add(self, example: FewShotExample) -> None:
        """Append an example to the bucket and flush to disk."""
        key = (example.workspace_id, example.persona)
        bucket = self._load(example.workspace_id, example.persona)
        bucket.append(example)
        self._cache[key] = bucket
        self._flush(example.workspace_id, example.persona, bucket)
        # Evict after adding so the file never exceeds max+1
        self.evict_if_needed(example.workspace_id, example.persona)

    def get_all(self, workspace_id: str, persona: str) -> List[FewShotExample]:
        """Return all examples for the given bucket (loads from disk if needed)."""
        return list(self._load(workspace_id, persona))

    def evict_if_needed(self, workspace_id: str, persona: str) -> int:
        """
        Evict examples until the bucket is within ``max_per_bucket``.

        Returns the number of examples evicted.
        """
        bucket = self._load(workspace_id, persona)
        if len(bucket) <= self._max:
            return 0

        now_ts = time.time()
        n_over = len(bucket) - self._max
        # Sort ascending by eviction score → evict the first n_over
        bucket.sort(key=lambda ex: _eviction_key(ex, now_ts))
        evicted_ids = {ex.id for ex in bucket[:n_over]}
        bucket = [ex for ex in bucket if ex.id not in evicted_ids]

        key = (workspace_id, persona)
        self._cache[key] = bucket
        self._flush(workspace_id, persona, bucket)

        log.info(
            "few_shot.evicted",
            workspace_id=workspace_id,
            persona=persona,
            n=n_over,
        )
        return n_over

    def delete(self, example_id: str) -> None:
        """Remove a single example by ID from whichever bucket it lives in.

        Searches all loaded buckets first; falls back to scanning disk if needed.
        """
        # Search loaded buckets
        for (ws, persona), bucket in list(self._cache.items()):
            new_bucket = [ex for ex in bucket if ex.id != example_id]
            if len(new_bucket) < len(bucket):
                self._cache[(ws, persona)] = new_bucket
                self._flush(ws, persona, new_bucket)
                return

        # Scan disk (the bucket may not be loaded yet)
        for ws_dir in self._root.iterdir() if self._root.exists() else []:
            if not ws_dir.is_dir():
                continue
            for persona_file in ws_dir.glob("*.json"):
                persona = persona_file.stem
                bucket  = self._load(ws_dir.name, persona)
                new_bucket = [ex for ex in bucket if ex.id != example_id]
                if len(new_bucket) < len(bucket):
                    self._cache[(ws_dir.name, persona)] = new_bucket
                    self._flush(ws_dir.name, persona, new_bucket)
                    return

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _bucket_path(self, workspace_id: str, persona: str) -> Path:
        return self._root / workspace_id / f"{persona}.json"

    def _load(self, workspace_id: str, persona: str) -> List[FewShotExample]:
        key = (workspace_id, persona)
        if key in self._cache:
            return self._cache[key]

        path = self._bucket_path(workspace_id, persona)
        if not path.exists():
            self._cache[key] = []
            return self._cache[key]

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            bucket = [_from_dict(d) for d in raw]
        except Exception as exc:
            log.warning("few_shot.load_failed", path=str(path), error=str(exc))
            bucket = []

        self._cache[key] = bucket
        return bucket

    def _flush(
        self,
        workspace_id: str,
        persona: str,
        bucket: List[FewShotExample],
    ) -> None:
        """Write ``bucket`` to disk atomically (write tmp → rename)."""
        path = self._bucket_path(workspace_id, persona)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        try:
            tmp.write_text(
                json.dumps([_to_dict(ex) for ex in bucket], indent=2),
                encoding="utf-8",
            )
            tmp.replace(path)
        except Exception as exc:
            log.error("few_shot.flush_failed", path=str(path), error=str(exc))
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
