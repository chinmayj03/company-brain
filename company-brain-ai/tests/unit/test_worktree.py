"""Unit tests for WorktreeManager (ADR-0052 P5)."""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

import pytest

from companybrain.harness.worktree import WorktreeError, WorktreeManager


def _git_init_with_two_commits(repo: Path) -> tuple[str, str]:
    """Lay out a tiny git repo with two distinct commits, return their SHAs."""
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    env_kwargs = {
        "cwd": repo, "check": True,
        "env": {**__import_os_environ(), "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@t",
                 "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t"},
    }
    (repo / "a.txt").write_text("alpha\n")
    subprocess.run(["git", "add", "."], **env_kwargs)
    subprocess.run(["git", "commit", "-q", "-m", "first"], **env_kwargs)
    sha_a = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                     cwd=repo).decode().strip()
    (repo / "b.txt").write_text("bravo\n")
    subprocess.run(["git", "add", "."], **env_kwargs)
    subprocess.run(["git", "commit", "-q", "-m", "second"], **env_kwargs)
    sha_b = subprocess.check_output(["git", "rev-parse", "HEAD"],
                                     cwd=repo).decode().strip()
    return sha_a, sha_b


def __import_os_environ() -> dict[str, str]:
    import os
    return dict(os.environ)


async def test_worktree_checks_out_at_pinned_commit(tmp_path: Path):
    """Entering with commit_sha=A should expose a tree at commit A's contents."""
    sha_a, sha_b = _git_init_with_two_commits(tmp_path)

    async with WorktreeManager(tmp_path, commit_sha=sha_a) as wt:
        assert (wt / "a.txt").exists()
        assert not (wt / "b.txt").exists()
        # Pinned to sha_a so HEAD inside the worktree resolves to sha_a.
        head = subprocess.check_output(
            ["git", "-C", str(wt), "rev-parse", "HEAD"]
        ).decode().strip()
        assert head == sha_a


async def test_worktree_concurrent_jobs_isolated(tmp_path: Path):
    """Two concurrent context managers don't fight over HEAD."""
    sha_a, sha_b = _git_init_with_two_commits(tmp_path)

    async def _check(commit: str, expected_file: str, missing_file: str) -> bool:
        async with WorktreeManager(tmp_path, commit_sha=commit) as wt:
            await asyncio.sleep(0.05)  # give the other coroutine room to interleave
            return (wt / expected_file).exists() and not (wt / missing_file).exists()

    job_a, job_b = await asyncio.gather(
        _check(sha_a, "a.txt", "b.txt"),
        _check(sha_b, "b.txt", "missing"),
    )
    assert job_a is True
    assert job_b is True


async def test_worktree_cleans_up_on_exit(tmp_path: Path):
    """The temp worktree directory is removed when the block ends."""
    _git_init_with_two_commits(tmp_path)
    captured: list[Path] = []

    async with WorktreeManager(tmp_path) as wt:
        captured.append(wt)
        assert wt.exists()

    assert not captured[0].exists()


async def test_worktree_raises_for_non_git_dir(tmp_path: Path):
    """A non-git directory short-circuits with WorktreeError."""
    with pytest.raises(WorktreeError):
        async with WorktreeManager(tmp_path, commit_sha="HEAD"):
            pass
