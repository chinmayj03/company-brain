"""
Workspace-scoped key-value persistence for tuning artifacts.

Tuning artifacts (glossary, few-shot bank metadata, etc.) are stored as
JSON files under {root}/{workspace_id}/{key}.json. Writes are atomic via
a write-to-tmp-then-rename pattern to avoid partial reads.
"""

from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Any


class WorkspaceTuningStore:
    """Persist workspace tuning artifacts (glossary, few-shot bank metadata, etc.)
    to JSON files under {root}/{workspace_id}/."""

    def __init__(self, root: Path):
        self._root = root
        self._lock = Lock()

    def get(self, workspace_id: str, key: str, default: Any = None) -> Any:
        path = self._root / workspace_id / f"{key}.json"
        if not path.exists():
            return default
        with path.open() as f:
            return json.load(f)

    def set(self, workspace_id: str, key: str, value: Any) -> None:
        d = self._root / workspace_id
        d.mkdir(parents=True, exist_ok=True)
        with self._lock:
            tmp = d / f"{key}.json.tmp"
            tmp.write_text(json.dumps(value, default=str))
            tmp.rename(d / f"{key}.json")

    def delete(self, workspace_id: str, key: str) -> None:
        path = self._root / workspace_id / f"{key}.json"
        with self._lock:
            if path.exists():
                path.unlink()

    def list_keys(self, workspace_id: str) -> list[str]:
        d = self._root / workspace_id
        if not d.exists():
            return []
        return [p.stem for p in d.glob("*.json")]


def get_tuning_store(root_path: str = ".brain/tuning") -> "WorkspaceTuningStore":
    return WorkspaceTuningStore(Path(root_path))
