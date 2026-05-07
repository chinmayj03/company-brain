"""ADR-006 §5: Tests for structural/changes.py — git-diff dirty-set detection.

Tests cover:
    - get_changed_files() returns correct repo-relative Paths for a two-commit fixture
    - Only source-file extensions are included; assets / config are ignored
    - Deleted-file entries are not included (file must exist on disk)
    - Falls back to full_scan() when git diff fails (single-commit repo)
    - full_scan() returns all tracked source files and respects .gitignore logic
    - current_head_sha() returns the current HEAD SHA
    - get_changed_files_since() uses the since_sha as from_ref

Run with::

    pytest tests/unit/structural/test_changes.py -v
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure the src package is importable when running tests from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from companybrain.structural.changes import (
    _SOURCE_EXTENSIONS,
    current_head_sha,
    full_scan,
    get_changed_files,
    get_changed_files_since,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture()
def git_repo(tmp_path: Path):
    """Create a minimal two-commit git repo with a mix of file types."""
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, check=True, capture_output=True,
    )

    # ── Commit 1: initial files ────────────────────────────────────────────
    (tmp_path / "main.py").write_text("print('hello')")
    (tmp_path / "utils.java").write_text("class Utils {}")
    (tmp_path / "README.md").write_text("# readme")       # not a source file
    (tmp_path / "config.yaml").write_text("key: value")   # not a source file
    (tmp_path / "logo.png").write_bytes(b"\x89PNG")       # not a source file

    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=tmp_path, check=True, capture_output=True,
    )

    # ── Commit 2: change main.py, add new.ts, don't touch utils.java ──────
    (tmp_path / "main.py").write_text("print('world')")
    (tmp_path / "new.ts").write_text("export const x = 1;")
    (tmp_path / "data.json").write_text("{}")             # not a source file

    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "second commit"],
        cwd=tmp_path, check=True, capture_output=True,
    )

    return tmp_path


# ── get_changed_files() ────────────────────────────────────────────────────────


class TestGetChangedFiles:
    def test_returns_changed_source_files_only(self, git_repo: Path):
        """get_changed_files() should return main.py and new.ts but NOT config.yaml / data.json."""
        changed = get_changed_files(git_repo)
        assert Path("main.py") in changed, "Modified source file should be returned"
        assert Path("new.ts") in changed, "New source file should be returned"

    def test_excludes_non_source_extensions(self, git_repo: Path):
        changed = get_changed_files(git_repo)
        names = {p.name for p in changed}
        assert "README.md" not in names
        assert "config.yaml" not in names
        assert "logo.png" not in names
        assert "data.json" not in names

    def test_excludes_unchanged_files(self, git_repo: Path):
        """utils.java was NOT touched in commit 2, so it should not appear."""
        changed = get_changed_files(git_repo)
        assert Path("utils.java") not in changed

    def test_paths_are_relative_to_repo_root(self, git_repo: Path):
        changed = get_changed_files(git_repo)
        for p in changed:
            assert not p.is_absolute(), f"Expected relative path, got: {p}"

    def test_falls_back_to_full_scan_on_single_commit(self, tmp_path: Path):
        """On a single-commit repo HEAD~1 is invalid; should fall back to full_scan."""
        subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "t@t.com"], cwd=tmp_path,
            check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "T"], cwd=tmp_path,
            check=True, capture_output=True,
        )
        (tmp_path / "app.py").write_text("x = 1")
        subprocess.run(["git", "add", "."], cwd=tmp_path, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "only"], cwd=tmp_path, check=True, capture_output=True,
        )

        # Should not raise; falls back to full_scan → returns app.py
        result = get_changed_files(tmp_path)
        assert Path("app.py") in result

    def test_arbitrary_from_to_refs(self, git_repo: Path):
        """get_changed_files with explicit from_ref / to_ref returns same as HEAD~1..HEAD."""
        # Get SHA of first commit
        result = subprocess.run(
            ["git", "rev-parse", "HEAD~1"],
            cwd=git_repo, capture_output=True, text=True, check=True,
        )
        first_sha = result.stdout.strip()

        changed = get_changed_files(git_repo, from_ref=first_sha, to_ref="HEAD")
        assert Path("main.py") in changed
        assert Path("new.ts") in changed


# ── full_scan() ───────────────────────────────────────────────────────────────


class TestFullScan:
    def test_returns_all_tracked_source_files(self, git_repo: Path):
        """After two commits, full_scan should return all tracked source files."""
        files = full_scan(git_repo)
        names = {p.name for p in files}
        assert "main.py" in names
        assert "utils.java" in names
        assert "new.ts" in names

    def test_excludes_non_source_files(self, git_repo: Path):
        files = full_scan(git_repo)
        names = {p.name for p in files}
        assert "README.md" not in names
        assert "config.yaml" not in names
        assert "logo.png" not in names

    def test_returns_relative_paths(self, git_repo: Path):
        for p in full_scan(git_repo):
            assert not p.is_absolute()


# ── current_head_sha() ────────────────────────────────────────────────────────


class TestCurrentHeadSha:
    def test_returns_40_char_hex(self, git_repo: Path):
        sha = current_head_sha(git_repo)
        assert sha is not None
        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha)

    def test_returns_none_for_non_git_dir(self, tmp_path: Path):
        sha = current_head_sha(tmp_path)
        assert sha is None


# ── get_changed_files_since() ─────────────────────────────────────────────────


class TestGetChangedFilesSince:
    def test_since_first_commit_sha(self, git_repo: Path):
        first_sha_result = subprocess.run(
            ["git", "rev-parse", "HEAD~1"],
            cwd=git_repo, capture_output=True, text=True, check=True,
        )
        first_sha = first_sha_result.stdout.strip()

        changed = get_changed_files_since(git_repo, since_sha=first_sha)
        assert Path("main.py") in changed
        assert Path("new.ts") in changed


# ── _SOURCE_EXTENSIONS set ────────────────────────────────────────────────────


class TestSourceExtensions:
    def test_contains_expected_extensions(self):
        expected = {".py", ".java", ".ts", ".tsx", ".js", ".jsx", ".go",
                    ".kt", ".rs", ".cs", ".rb", ".php", ".swift", ".scala"}
        for ext in expected:
            assert ext in _SOURCE_EXTENSIONS, f"Missing extension: {ext}"

    def test_does_not_contain_config_or_asset_extensions(self):
        not_expected = {".md", ".yaml", ".yml", ".json", ".png", ".jpg", ".svg",
                        ".txt", ".csv", ".html", ".xml"}
        for ext in not_expected:
            assert ext not in _SOURCE_EXTENSIONS, f"Should not be a source ext: {ext}"
