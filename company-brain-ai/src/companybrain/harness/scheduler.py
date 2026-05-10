"""Cron-like scheduled extractions (ADR-0052 P6).

APScheduler is the workhorse: jobs persist via ``SQLAlchemyJobStore`` against
the ``scheduled_tasks`` table introduced in V13. The scheduler module exposes
a thin async surface that the CLI and the harness loop consume.

The store URL is read from ``DATABASE_URL`` once at scheduler-start time. The
scheduler is created lazily so importing this module never opens a Postgres
connection — callers that just want :func:`build_jobstore_url` for tests
won't trigger network IO.

When APScheduler isn't installed (CI without the dep), :func:`get_scheduler`
raises :class:`MissingSchedulerDependency` so tests can detect the gap and
skip cleanly.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Optional

import structlog

log = structlog.get_logger(__name__)


class MissingSchedulerDependency(RuntimeError):
    """APScheduler isn't installed; install via ``pip install apscheduler``."""


# ── jobstore URL ─────────────────────────────────────────────────────────────

def build_jobstore_url() -> str:
    """Translate our ``DATABASE_URL`` (asyncpg-flavoured) to a sync URL.

    APScheduler's ``SQLAlchemyJobStore`` uses synchronous SQLAlchemy under
    the hood, so the ``+asyncpg`` dialect that the rest of the AI service
    speaks would explode at scheduler-start. We strip the suffix here.
    """
    raw = os.environ.get("DATABASE_URL", "postgresql://localhost/companybrain")
    return raw.replace("postgresql+asyncpg://", "postgresql://")


# ── scheduled-task description ───────────────────────────────────────────────

@dataclass
class ScheduledTask:
    """Stored description of a cron-triggered extraction."""
    id: str
    cron: str
    repo: str
    endpoint: str
    method: str = "GET"
    workspace_id: str = ""
    next_run_at: Optional[datetime] = None
    last_run_at: Optional[datetime] = None
    last_run_ok: Optional[bool] = None
    extra: dict[str, Any] = field(default_factory=dict)


# ── scheduler singleton ──────────────────────────────────────────────────────

_scheduler: Any | None = None
_runner: Callable[..., Awaitable[Any]] | None = None


def _default_runner(*, repo: str, endpoint: str, method: str,
                    workspace_id: str, **extra: Any) -> Awaitable[Any]:
    """Default APScheduler trigger body — calls ``brain index`` programmatically.

    Imported lazily inside the function so the scheduler module is import-safe
    even on machines where the pipeline deps haven't been installed.
    """
    async def _run() -> dict[str, Any]:
        from pathlib import Path

        from companybrain.cli_helpers.headless import run_index_headless

        log.info("scheduler.run.start", repo=repo, endpoint=endpoint,
                 method=method, workspace_id=workspace_id)
        payload, exit_code = await run_index_headless(
            repo_path=Path(repo).resolve(),
            branch=str(extra.get("branch", "main")),
            workspace_id=workspace_id or "00000000-0000-0000-0000-000000000001",
            endpoints=f"{method} {endpoint}" if endpoint else None,
            repo_name=str(extra.get("repo_name", "monorepo")),
            dry_run=False,
        )
        ok = exit_code == 0
        log.info("scheduler.run.done", repo=repo, ok=ok, exit_code=exit_code)
        return {"ok": ok, "payload": payload, "exit_code": exit_code}

    return _run()


def configure_runner(fn: Callable[..., Awaitable[Any]]) -> None:
    """Override the default runner — used by tests to inject a stub."""
    global _runner
    _runner = fn


def get_scheduler() -> Any:
    """Build (or return) the singleton :class:`AsyncIOScheduler`.

    Lazily imports APScheduler so the rest of the harness can be used without
    the dependency installed; raises :class:`MissingSchedulerDependency` when
    you actually try to schedule something on a CI box that left it out.
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    try:
        from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
    except ImportError as exc:                 # pragma: no cover
        raise MissingSchedulerDependency(
            "apscheduler is not installed. `pip install apscheduler` "
            "or rebuild the AI service image."
        ) from exc

    _scheduler = AsyncIOScheduler(jobstores={
        "default": SQLAlchemyJobStore(
            url=build_jobstore_url(),
            tablename="scheduled_tasks",
        ),
    })
    _scheduler.start(paused=False)
    log.info("scheduler.start.ok", url=build_jobstore_url())
    return _scheduler


# ── public API ───────────────────────────────────────────────────────────────

async def schedule(
    *,
    name: str,
    repo: str,
    endpoint: str,
    method: str,
    cron: str,
    workspace_id: str = "",
    extra: Optional[dict[str, Any]] = None,
) -> str:
    """Register or replace a cron-triggered extraction; returns the job id."""
    from apscheduler.triggers.cron import CronTrigger

    sched = get_scheduler()
    runner = _runner or _default_runner

    job = sched.add_job(
        runner,
        trigger=CronTrigger.from_crontab(cron),
        kwargs={
            "repo": repo, "endpoint": endpoint, "method": method,
            "workspace_id": workspace_id, **(extra or {}),
        },
        id=name,
        replace_existing=True,
    )
    log.info("scheduler.schedule.ok", id=job.id, cron=cron, repo=repo,
             endpoint=endpoint, method=method)
    return str(job.id)


def list_jobs() -> list[ScheduledTask]:
    """List every stored job. Returns an empty list when the scheduler is paused."""
    try:
        sched = get_scheduler()
    except MissingSchedulerDependency:          # pragma: no cover
        return []
    out: list[ScheduledTask] = []
    for job in sched.get_jobs():
        kw = job.kwargs or {}
        out.append(ScheduledTask(
            id=str(job.id),
            cron=str(getattr(job, "trigger", "")),
            repo=str(kw.get("repo", "")),
            endpoint=str(kw.get("endpoint", "")),
            method=str(kw.get("method", "GET")),
            workspace_id=str(kw.get("workspace_id", "")),
            next_run_at=getattr(job, "next_run_time", None),
            extra={k: v for k, v in kw.items()
                   if k not in {"repo", "endpoint", "method", "workspace_id"}},
        ))
    return out


def cancel(job_id: str) -> bool:
    """Remove a job. Returns False if it wasn't there."""
    try:
        sched = get_scheduler()
    except MissingSchedulerDependency:          # pragma: no cover
        return False
    try:
        sched.remove_job(job_id)
    except Exception:
        return False
    log.info("scheduler.cancel.ok", id=job_id)
    return True


async def run_now(job_id: str) -> dict[str, Any]:
    """Fire a job's body once, immediately, off the cron trigger.

    The result dict carries the runner's return value so callers can assert
    on it from acceptance tests.
    """
    sched = get_scheduler()
    job = sched.get_job(job_id)
    if job is None:
        return {"ok": False, "error": f"unknown job {job_id!r}"}
    runner = _runner or _default_runner
    started = datetime.now(tz=timezone.utc)
    try:
        result = await runner(**(job.kwargs or {}))
        ok = bool(result.get("ok", True)) if isinstance(result, dict) else True
        return {"ok": ok, "started_at": started.isoformat(), "result": result}
    except Exception as exc:                                       # pragma: no cover
        log.warning("scheduler.run_now.error", id=job_id, error=str(exc))
        return {"ok": False, "started_at": started.isoformat(), "error": str(exc)}


# ── lifecycle helpers ────────────────────────────────────────────────────────

def shutdown(wait: bool = False) -> None:
    """Stop the scheduler. Safe to call from atexit handlers."""
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=wait)
    except Exception:                                              # pragma: no cover
        pass
    _scheduler = None
