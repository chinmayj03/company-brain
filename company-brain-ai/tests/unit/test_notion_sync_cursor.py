"""
Unit tests for companybrain.connectors.notion.sync_cursor.NotionSyncCursor.

All tests are pure Python — no network calls.
Uses tmp_path pytest fixture for file isolation.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from companybrain.connectors.notion.sync_cursor import NotionSyncCursor


class TestNotionSyncCursor:
    # ── get_cursor ───────────────────────────────────────────────────────────

    def test_get_cursor_returns_none_when_file_missing(self, tmp_path: Path):
        cursor = NotionSyncCursor(tmp_path / "subdir" / "cursor.json")
        assert cursor.get_cursor("ws1") is None

    def test_get_cursor_returns_none_when_workspace_absent(self, tmp_path: Path):
        state_file = tmp_path / "cursor.json"
        state_file.write_text(json.dumps({"other_ws": "2025-01-01"}))
        cursor = NotionSyncCursor(state_file)
        assert cursor.get_cursor("ws1") is None

    def test_get_cursor_returns_none_on_corrupt_json(self, tmp_path: Path):
        state_file = tmp_path / "cursor.json"
        state_file.write_text("not valid json!!!")
        cursor = NotionSyncCursor(state_file)
        assert cursor.get_cursor("ws1") is None

    # ── set_cursor + get_cursor round-trip ──────────────────────────────────

    def test_set_then_get_returns_same_timestamp(self, tmp_path: Path):
        cursor = NotionSyncCursor(tmp_path / "cursor.json")
        cursor.set_cursor("ws1", "2026-01-01T00:00:00+00:00")
        assert cursor.get_cursor("ws1") == "2026-01-01T00:00:00+00:00"

    def test_multiple_workspaces_are_isolated(self, tmp_path: Path):
        cursor = NotionSyncCursor(tmp_path / "cursor.json")
        cursor.set_cursor("ws1", "2026-01-01")
        cursor.set_cursor("ws2", "2026-06-15")
        assert cursor.get_cursor("ws1") == "2026-01-01"
        assert cursor.get_cursor("ws2") == "2026-06-15"

    def test_set_cursor_overwrites_previous_value(self, tmp_path: Path):
        cursor = NotionSyncCursor(tmp_path / "cursor.json")
        cursor.set_cursor("ws1", "2026-01-01")
        cursor.set_cursor("ws1", "2026-03-15")
        assert cursor.get_cursor("ws1") == "2026-03-15"

    def test_set_cursor_does_not_overwrite_other_workspaces(self, tmp_path: Path):
        cursor = NotionSyncCursor(tmp_path / "cursor.json")
        cursor.set_cursor("ws1", "2026-01-01")
        cursor.set_cursor("ws2", "2026-02-01")
        # Update ws1 — ws2 must remain unchanged
        cursor.set_cursor("ws1", "2026-05-01")
        assert cursor.get_cursor("ws2") == "2026-02-01"

    # ── parent directory creation ────────────────────────────────────────────

    def test_parent_dir_created_automatically(self, tmp_path: Path):
        state_file = tmp_path / "deep" / "nested" / "cursor.json"
        cursor = NotionSyncCursor(state_file)
        cursor.set_cursor("ws1", "2026-01-01")
        assert state_file.exists()

    # ── now_iso ──────────────────────────────────────────────────────────────

    def test_now_iso_is_string(self, tmp_path: Path):
        cursor = NotionSyncCursor(tmp_path / "cursor.json")
        ts = cursor.now_iso()
        assert isinstance(ts, str)
        # Should be a valid ISO timestamp with timezone info
        assert "T" in ts
        assert "+" in ts or "Z" in ts

    def test_now_iso_advances_over_time(self, tmp_path: Path):
        import time
        cursor = NotionSyncCursor(tmp_path / "cursor.json")
        t1 = cursor.now_iso()
        time.sleep(0.01)
        t2 = cursor.now_iso()
        # Lexicographic comparison works for ISO timestamps
        assert t2 >= t1
