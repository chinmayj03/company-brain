"""Per-job git worktrees (ADR-0052 P5).

Two concurrent extraction jobs against the same repo at different commits used
to fight over ``HEAD`` — checkout B would silently move A's working tree out
from under it. ``WorktreeManager`` solves that by running every job in its own
``git worktree add`` directory, then cleaning the worktree up on exit.

Use as an async context manager:

    async with WorktreeManager(repo_path, commit_sha="abc123") as wt:
        # `wt` is the temp worktree path, checked out at abc123 and isolated
        # from any other concurrent run.
        await do_extraction(wt)

If ``commit_sha`` is None the manager defers to the source repo's current HEAD,
which mirrors the legacy single-job behaviour. Use that escape hatch only for
single-tenant CLI runs; concurrent workloads always pin a commit.

Cleanup is best-effort. A failed ``git worktree remove`` is logged but never
raised — the worker pool should keep running even if one tempdir leaks.
"""
from __future__ import annotations

import asyncio
import shutil
import tempfile
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)


class WorktreeError(RuntimeError):
    """Raised when ``git worktree add`` fails. Cleanup happens before re-raise."""


class WorktreeManager:
    """Async context manager for an isolated git worktree.

    Parameters
    ----------
    repo_path
        Path to the source repo. Must be a git checkout — non-git directories
        raise on ``__aenter__``.
    commit_sha
        Optional commit / branch / ref to check out in the worktree. ``None``
        leaves the worktree at the same HEAD as the source.
    prefix
        Prefix for the temporary worktree directory name. Defaults to
        ``"brain-wt-"``; override only for tests that need to assert on the
        path layout.
    """

    def __init__(
        self,
        repo_path: Path | str,
        *,
        commit_sha: str | None = None,
        prefix: str = "brain-wt-",
    ):
        self.repo_path = Path(repo_path).resolve()
        self.commit_sha = commit_sha
        self._prefix = prefix
        self._wt_path: Path | None = None

    async def __aenter__(self) -> Path:
        if not (self.repo_path / ".git").exists():
            raise WorktreeError(
                f"Not a git repository: {self.repo_path}. WorktreeManager only "
                "operates on git checkouts."
            )

        self._wt_path = Path(tempfile.mkdtemp(prefix=self._prefix))
        # Match the suffix conventions our cleanup uses.
        target = str(self._wt_path)

        cmd: list[str] = ["git", "-C", str(self.repo_path),
                           "worktree", "add", "--detach", target]
        if self.commit_sha:
            cmd.append(self.commit_sha)

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            self._best_effort_rmtree()
            raise WorktreeError(
                f"git worktree add failed (exit {proc.returncode}): "
                f"{stderr.decode(errors='replace').strip()}"
            )
        log.debug(
            "worktree.add",
            source=str(self.repo_path),
            wt=str(self._wt_path),
            commit=self.commit_sha,
            stdout=stdout.decode(errors="replace").strip(),
        )
        return self._wt_path

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object | None,
    ) -> None:
        if self._wt_path is None:
            return
        target = str(self._wt_path)
        # `git worktree remove --force` deletes the directory AND prunes the
        # registry entry the source repo holds. We pair it with rmtree as a
        # belt-and-braces cleanup for the (rare) case where git refuses.
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "-C", str(self.repo_path),
                "worktree", "remove", "--force", target,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                log.warning(
                    "worktree.remove.failed",
                    wt=target,
                    error=stderr.decode(errors="replace").strip(),
                )
        except (OSError, asyncio.CancelledError) as exc_inner:
            log.warning("worktree.remove.exception", wt=target, error=str(exc_inner))

        # Always try to clean the directory itself — even when git refuses,
        # the temp dir should not linger.
        self._best_effort_rmtree()

    # ── internals ──────────────────────────────────────────────────────────

    def _best_effort_rmtree(self) -> None:
        if self._wt_path and self._wt_path.exists():
            try:
                shutil.rmtree(self._wt_path, ignore_errors=True)
            except OSError as exc:
                log.warning("worktree.rmtree_failed", wt=str(self._wt_path),
                            error=str(exc))


__all__ = ["WorktreeManager", "WorktreeError"]
