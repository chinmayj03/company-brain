"""ADR-006 §7: Tests for structural/indexer.py — hash-diff incremental indexer.

Tests cover:
    - Hash-diff skip: files whose SHA-256 matches the DB record are skipped
    - Hash-diff parse: files with a changed (or unknown) hash are re-parsed
    - On first run (empty DB), all files are parsed (no hashes to match)
    - Full vs incremental mode: correct file sets are selected
    - IndexResult counters: files_parsed, files_skipped, total_files
    - No-op run: if no files changed, returns immediately without DB calls

All DB access is mocked so no real Postgres connection is needed.

Run with::

    pytest tests/unit/structural/test_indexer.py -v
"""

from __future__ import annotations

import hashlib
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from companybrain.structural.indexer import IndexResult, StructuralIndexer

WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"
DB_URL = "postgresql://fake/fake"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _make_indexer(**kwargs) -> StructuralIndexer:
    return StructuralIndexer(
        db_url=DB_URL,
        workspace_id=WORKSPACE_ID,
        max_workers=1,
        **kwargs,
    )


def _make_db_mock(known_hashes: dict[str, str]):
    """Return a mock psycopg2 connection whose cursor returns given hash dict."""
    cursor = MagicMock()
    # _load_file_hashes → fetchall returns list of (file_path, file_hash) tuples
    cursor.fetchall.return_value = list(known_hashes.items())
    conn = MagicMock()
    conn.cursor.return_value = cursor
    return conn


@pytest.fixture()
def git_repo(tmp_path: Path):
    """Minimal two-commit git repo with one Python file."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    for cfg in [
        ["git", "config", "user.email", "t@t.com"],
        ["git", "config", "user.name", "T"],
    ]:
        subprocess.run(cfg, cwd=tmp_path, check=True, capture_output=True)

    (tmp_path / "app.py").write_text("x = 1")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=tmp_path, check=True, capture_output=True
    )

    (tmp_path / "app.py").write_text("x = 2")   # modify
    (tmp_path / "helper.py").write_text("y = 3")  # add
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "second"], cwd=tmp_path, check=True, capture_output=True
    )
    return tmp_path


# ── IndexResult unit tests ────────────────────────────────────────────────────


class TestIndexResult:
    def test_total_files_sums_correctly(self):
        r = IndexResult(
            workspace_id=WORKSPACE_ID,
            repo_root="/tmp/fake",
            mode="incremental",
            files_parsed=3,
            files_skipped=7,
            files_failed=1,
        )
        assert r.total_files == 11

    def test_default_counters_are_zero(self):
        r = IndexResult(workspace_id=WORKSPACE_ID, repo_root="/tmp", mode="full")
        assert r.files_parsed == 0
        assert r.files_skipped == 0
        assert r.files_failed == 0
        assert r.nodes_upserted == 0
        assert r.edges_upserted == 0


# ── Hash-diff skip logic ──────────────────────────────────────────────────────


class TestHashDiffSkipLogic:
    """
    Test _index_files() hash-diff filtering in isolation by mocking:
      - psycopg2.connect   → returns a fake conn with known hashes
      - parse_file         → returns empty nodes/edges (we only care about counters)
      - ProcessPoolExecutor → runs synchronously
    """

    def _run_index_files(
        self, tmp_path: Path, files: set[Path], known_hashes: dict[str, str]
    ) -> IndexResult:
        """Helper: run _index_files() with mocked DB and parser."""
        indexer = _make_indexer()
        conn = _make_db_mock(known_hashes)
        result = IndexResult(
            workspace_id=WORKSPACE_ID, repo_root=str(tmp_path), mode="test"
        )

        # Patch out DB connect, ProcessPoolExecutor (→ ThreadPoolExecutor so mock
        # patches survive across the executor boundary), and the parse worker.
        with (
            patch.object(indexer, "_connect", return_value=conn),
            patch("companybrain.structural.indexer.ProcessPoolExecutor", ThreadPoolExecutor),
            patch("companybrain.structural.indexer._parse_worker", return_value=([], [])),
            patch("companybrain.structural.indexer._set_workspace"),
            patch.object(indexer, "_upsert_nodes", return_value=0),
            patch.object(indexer, "_upsert_edges", return_value=0),
        ):
            return indexer._index_files(files, tmp_path, result)

    def test_unchanged_file_is_skipped(self, tmp_path: Path):
        """File whose SHA-256 matches the DB hash → files_skipped += 1."""
        f = tmp_path / "a.py"
        f.write_text("print('hello')")
        current_hash = _sha256(f)

        result = self._run_index_files(
            tmp_path,
            {Path("a.py")},
            {"a.py": current_hash},  # DB already has this hash
        )
        assert result.files_skipped == 1
        assert result.files_parsed == 0

    def test_changed_file_is_parsed(self, tmp_path: Path):
        """File whose SHA-256 differs from the DB hash → files_parsed += 1."""
        f = tmp_path / "a.py"
        f.write_text("print('world')")

        result = self._run_index_files(
            tmp_path,
            {Path("a.py")},
            {"a.py": "old_stale_hash_value"},  # DB has a different hash
        )
        assert result.files_parsed == 1
        assert result.files_skipped == 0

    def test_first_run_empty_db_parses_all(self, tmp_path: Path):
        """On first run, DB returns no hashes → all files are parsed."""
        for name in ("a.py", "b.py", "c.py"):
            (tmp_path / name).write_text("x = 1")

        result = self._run_index_files(
            tmp_path,
            {Path("a.py"), Path("b.py"), Path("c.py")},
            {},  # empty DB
        )
        assert result.files_parsed == 3
        assert result.files_skipped == 0

    def test_mix_of_changed_and_unchanged(self, tmp_path: Path):
        """Some files changed, some unchanged — correct split."""
        (tmp_path / "a.py").write_text("x = 1")
        (tmp_path / "b.py").write_text("y = 2")
        (tmp_path / "c.py").write_text("z = 3")

        hash_a = _sha256(tmp_path / "a.py")
        hash_b = _sha256(tmp_path / "b.py")

        result = self._run_index_files(
            tmp_path,
            {Path("a.py"), Path("b.py"), Path("c.py")},
            {
                "a.py": hash_a,      # unchanged
                "b.py": hash_b,      # unchanged
                # c.py not in DB → parse
            },
        )
        assert result.files_skipped == 2
        assert result.files_parsed == 1

    def test_missing_file_on_disk_is_ignored(self, tmp_path: Path):
        """A file listed in affected set that no longer exists is silently skipped."""
        # "ghost.py" is in the affected set but not written to disk
        result = self._run_index_files(
            tmp_path,
            {Path("ghost.py")},
            {},
        )
        # Neither skipped nor parsed — just silently dropped
        assert result.files_parsed == 0
        assert result.files_skipped == 0

    def test_empty_affected_set_returns_immediately(self, tmp_path: Path):
        """If there are no files to process, _index_files should return quickly."""
        result = self._run_index_files(tmp_path, set(), {})
        assert result.files_parsed == 0
        assert result.files_skipped == 0


# ── run_incremental() no-op path ──────────────────────────────────────────────


class TestRunIncrementalNoOp:
    def test_no_changed_files_returns_without_db_connect(self, git_repo: Path):
        """If get_changed_files returns empty set, indexer should short-circuit."""
        indexer = _make_indexer()

        with (
            patch("companybrain.structural.indexer.get_changed_files", return_value=set()),
            patch("companybrain.structural.indexer.current_head_sha", return_value="abc123"),
            patch.object(indexer, "_connect") as mock_connect,
        ):
            result = indexer.run_incremental(git_repo)

        # Should NOT open a DB connection when there's nothing to do
        mock_connect.assert_not_called()
        assert result.files_parsed == 0
        assert result.last_sha == "abc123"
        assert result.mode == "incremental"


# ── run_incremental() with changes ────────────────────────────────────────────


class TestRunIncrementalWithChanges:
    def test_dirty_set_plus_dependents_are_processed(self, git_repo: Path):
        """Dirty files union dependents should all be fed to _index_files."""
        indexer = _make_indexer()
        conn = _make_db_mock({})  # empty DB — parse everything

        with (
            patch("companybrain.structural.indexer.get_changed_files",
                  return_value={Path("app.py")}),
            patch("companybrain.structural.indexer.find_dependents",
                  return_value={Path("helper.py")}),
            patch("companybrain.structural.indexer.current_head_sha", return_value="sha1"),
            patch.object(indexer, "_connect", return_value=conn),
            patch("companybrain.structural.indexer.ProcessPoolExecutor", ThreadPoolExecutor),
            patch("companybrain.structural.indexer._parse_worker", return_value=([], [])),
            patch("companybrain.structural.indexer._set_workspace"),
            patch.object(indexer, "_upsert_nodes", return_value=0),
            patch.object(indexer, "_upsert_edges", return_value=0),
        ):
            # Write the files so hash-diff doesn't discard them
            (git_repo / "app.py").write_text("x = 2")
            (git_repo / "helper.py").write_text("y = 3")

            result = indexer.run_incremental(git_repo)

        # Both dirty (app.py) and dependent (helper.py) should be processed
        assert result.total_files >= 2  # at least parsed + skipped + failed

    def test_since_sha_uses_get_changed_files_since(self, git_repo: Path):
        """When since_sha is provided, get_changed_files_since() must be called."""
        indexer = _make_indexer()
        conn = _make_db_mock({})

        with (
            patch("companybrain.structural.indexer.get_changed_files_since",
                  return_value=set()) as mock_since,
            patch("companybrain.structural.indexer.current_head_sha", return_value="abc"),
            patch.object(indexer, "_connect", return_value=conn),
        ):
            result = indexer.run_incremental(git_repo, since_sha="deadbeef")

        mock_since.assert_called_once_with(git_repo.resolve(), "deadbeef")
        assert result.mode == "incremental"


# ── run_full_index() ─────────────────────────────────────────────────────────


class TestRunFullIndex:
    def test_mode_is_full(self, git_repo: Path):
        indexer = _make_indexer()
        conn = _make_db_mock({})

        with (
            patch("companybrain.structural.indexer.full_scan",
                  return_value=set()),
            patch("companybrain.structural.indexer.current_head_sha", return_value="sha"),
            patch.object(indexer, "_connect", return_value=conn),
            patch("companybrain.structural.indexer.ProcessPoolExecutor", ThreadPoolExecutor),
            patch("companybrain.structural.indexer._set_workspace"),
            patch.object(indexer, "_upsert_nodes", return_value=0),
            patch.object(indexer, "_upsert_edges", return_value=0),
        ):
            result = indexer.run_full_index(git_repo)

        assert result.mode == "full"

    def test_full_scan_is_called(self, git_repo: Path):
        """run_full_index() must call full_scan(), not get_changed_files()."""
        indexer = _make_indexer()
        conn = _make_db_mock({})

        with (
            patch("companybrain.structural.indexer.full_scan",
                  return_value=set()) as mock_scan,
            patch("companybrain.structural.indexer.get_changed_files") as mock_incremental,
            patch("companybrain.structural.indexer.current_head_sha", return_value="sha"),
            patch.object(indexer, "_connect", return_value=conn),
            patch("companybrain.structural.indexer.ProcessPoolExecutor", ThreadPoolExecutor),
            patch("companybrain.structural.indexer._set_workspace"),
            patch.object(indexer, "_upsert_nodes", return_value=0),
            patch.object(indexer, "_upsert_edges", return_value=0),
        ):
            indexer.run_full_index(git_repo)

        mock_scan.assert_called_once()
        mock_incremental.assert_not_called()
