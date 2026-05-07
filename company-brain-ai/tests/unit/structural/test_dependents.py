"""ADR-006 §6: Tests for structural/dependents.py — reverse-import BFS expansion.

Tests cover:
    - find_dependents() returns files that import from dirty files
    - Transitive imports are found up to max_hops
    - Files already in dirty_files are NOT returned
    - Hop cap is respected (files beyond max_hops are excluded)
    - Empty dirty set returns empty set (fast path)
    - Files that no longer exist on disk are skipped
    - find_dependents_from_qualified_names() resolves qualified names to file paths first

Uses a mock psycopg2 connection and cursor to avoid a real database.

Run with::

    pytest tests/unit/structural/test_dependents.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from companybrain.structural.dependents import (
    DEFAULT_MAX_HOPS,
    find_dependents,
    find_dependents_from_qualified_names,
)

WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"


# ── Helper to build a mock conn/cursor ────────────────────────────────────────


def _make_conn(query_results: list[list[tuple]]):
    """Build a mock psycopg2 connection whose cursor.fetchall() returns each
    element of query_results in sequence (one per execute() call)."""
    cursor = MagicMock()
    cursor.fetchall.side_effect = query_results
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


# ── find_dependents() — basic cases ───────────────────────────────────────────


class TestFindDependents:
    def test_empty_dirty_files_returns_empty(self, tmp_path: Path):
        """Fast path: no dirty files → no DB calls, empty result."""
        conn, cursor = _make_conn([])
        result = find_dependents(conn, WORKSPACE_ID, set(), tmp_path)
        assert result == set()
        cursor.execute.assert_not_called()

    def test_direct_importer_found(self, tmp_path: Path):
        """A file that directly imports a dirty file should be returned."""
        # Arrange: a.py is dirty; b.py imports a.py
        (tmp_path / "a.py").write_text("x = 1")
        (tmp_path / "b.py").write_text("from a import x")

        # DB returns b.py as importer of a.py
        conn, cursor = _make_conn([
            [("b.py",)],  # hop 1: b.py imports a.py
            [],           # hop 2 (frontier = b.py): nobody imports b.py
        ])

        dirty = {Path("a.py")}
        result = find_dependents(conn, WORKSPACE_ID, dirty, tmp_path, max_hops=2)

        assert Path("b.py") in result

    def test_dirty_files_not_returned(self, tmp_path: Path):
        """Files already in dirty_files must not appear in the returned set."""
        (tmp_path / "a.py").write_text("x = 1")

        # DB echoes a.py back (simulates a self-import edge — shouldn't happen
        # in practice, but we must be robust)
        conn, cursor = _make_conn([
            [("a.py",)],
            [],
        ])
        dirty = {Path("a.py")}
        result = find_dependents(conn, WORKSPACE_ID, dirty, tmp_path, max_hops=2)

        # a.py was already in dirty → must not appear in extra
        assert Path("a.py") not in result

    def test_transitive_importer_found(self, tmp_path: Path):
        """BFS expansion should follow the import chain up to max_hops hops."""
        # a.py <- b.py <- c.py (two hops)
        for name in ("a.py", "b.py", "c.py"):
            (tmp_path / name).write_text("")

        conn, cursor = _make_conn([
            [("b.py",)],   # hop 1: b.py imports a.py
            [("c.py",)],   # hop 2: c.py imports b.py
            [],            # hop 3 (if attempted)
        ])

        dirty = {Path("a.py")}
        result = find_dependents(conn, WORKSPACE_ID, dirty, tmp_path, max_hops=3)

        assert Path("b.py") in result
        assert Path("c.py") in result

    def test_hop_cap_respected(self, tmp_path: Path):
        """Importer beyond max_hops should NOT be returned."""
        # a.py <- b.py <- c.py (two hops), but max_hops=1 → only b.py
        for name in ("a.py", "b.py", "c.py"):
            (tmp_path / name).write_text("")

        conn, cursor = _make_conn([
            [("b.py",)],  # hop 1
        ])

        dirty = {Path("a.py")}
        result = find_dependents(conn, WORKSPACE_ID, dirty, tmp_path, max_hops=1)

        assert Path("b.py") in result
        assert Path("c.py") not in result

    def test_nonexistent_dependent_skipped(self, tmp_path: Path):
        """If the DB references a file that no longer exists on disk, skip it."""
        (tmp_path / "a.py").write_text("")
        # b.py is NOT created on disk

        conn, cursor = _make_conn([
            [("b.py",)],  # b.py in DB but not on disk
            [],
        ])

        dirty = {Path("a.py")}
        result = find_dependents(conn, WORKSPACE_ID, dirty, tmp_path, max_hops=2)

        assert Path("b.py") not in result

    def test_visited_set_prevents_infinite_loop(self, tmp_path: Path):
        """A cycle in import graph (a→b→a) must not cause an infinite loop."""
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")

        # Simulates cyclic imports: b imports a, a imports b
        conn, cursor = _make_conn([
            [("b.py",)],   # hop 1: b imports a
            [("a.py",)],   # hop 2: a imports b — but a is already visited
            [],
        ])

        dirty = {Path("a.py")}
        result = find_dependents(conn, WORKSPACE_ID, dirty, tmp_path, max_hops=3)

        # b.py should be found; a.py was already in visited → not in extras
        assert Path("b.py") in result
        assert Path("a.py") not in result

    def test_default_max_hops_is_three(self):
        assert DEFAULT_MAX_HOPS == 3

    def test_returns_paths_relative_to_repo_root(self, tmp_path: Path):
        """Returned paths must be relative (not absolute)."""
        (tmp_path / "b.py").write_text("")

        conn, cursor = _make_conn([
            [("b.py",)],
            [],
        ])

        dirty = {Path("a.py")}
        result = find_dependents(conn, WORKSPACE_ID, dirty, tmp_path)

        for p in result:
            assert not p.is_absolute(), f"Expected relative path: {p}"


# ── find_dependents_from_qualified_names() ────────────────────────────────────


class TestFindDependentsFromQualifiedNames:
    def test_empty_qualified_names_returns_empty(self, tmp_path: Path):
        conn, cursor = _make_conn([])
        result = find_dependents_from_qualified_names(
            conn, WORKSPACE_ID, set(), tmp_path
        )
        assert result == set()
        cursor.execute.assert_not_called()

    def test_resolves_qualified_names_to_file_paths(self, tmp_path: Path):
        """First query resolves qualified names → file paths; second query does BFS."""
        (tmp_path / "a.py").write_text("")
        (tmp_path / "b.py").write_text("")

        # cursor.fetchall() is called twice:
        # 1. resolve qualified names → a.py
        # 2. BFS hop 1 → b.py imports a.py
        # 3. BFS hop 2 → no more importers
        conn, cursor = _make_conn([
            [("a.py",)],   # resolution query
            [("b.py",)],   # BFS hop 1
            [],            # BFS hop 2
        ])

        result = find_dependents_from_qualified_names(
            conn, WORKSPACE_ID,
            {"a.py::MyClass.my_method"},
            tmp_path,
        )

        assert Path("b.py") in result

    def test_returns_empty_when_qualified_names_not_in_db(self, tmp_path: Path):
        """If the resolve query returns nothing, no BFS is run."""
        conn, cursor = _make_conn([
            [],   # resolve query → no rows
        ])

        result = find_dependents_from_qualified_names(
            conn, WORKSPACE_ID,
            {"nonexistent.py::Thing"},
            tmp_path,
        )

        assert result == set()
        # Only one execute call (the resolve query)
        assert cursor.execute.call_count == 1
