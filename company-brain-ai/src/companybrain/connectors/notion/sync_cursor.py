"""
B1.4 Notion connector — incremental sync cursor.

Persists a per-workspace last-edited-time cursor to a JSON file.
The cursor is used to filter Notion search results to only pages
edited since the previous sync.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


class NotionSyncCursor:
    """
    Tracks last-edited-time cursor per workspace for incremental sync.

    The state file is a JSON object mapping workspace_id → ISO timestamp.
    Missing file or parse errors are treated as "no cursor" (full sync).
    """

    def __init__(self, state_path: Path) -> None:
        self._path = state_path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    # ── Public API ──────────────────────────────────────────────────────────

    def get_cursor(self, workspace_id: str) -> str | None:
        """
        Return the ISO timestamp of the last successful sync for `workspace_id`,
        or None if no cursor exists (triggers a full sync).
        """
        try:
            data = json.loads(self._path.read_text())
            return data.get(workspace_id)
        except (FileNotFoundError, json.JSONDecodeError):
            return None

    def set_cursor(self, workspace_id: str, timestamp: str) -> None:
        """
        Persist `timestamp` as the cursor for `workspace_id`.
        Merges with existing cursors for other workspaces.
        """
        try:
            data = json.loads(self._path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        data[workspace_id] = timestamp
        self._path.write_text(json.dumps(data, indent=2))

    def now_iso(self) -> str:
        """Return the current UTC time as an ISO 8601 string."""
        return datetime.now(timezone.utc).isoformat()
