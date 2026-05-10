"""Unit tests for the scheduler harness module (ADR-0052 P6).

We don't stand up a real Postgres-backed APScheduler in unit tests — that
belongs in acceptance. Instead we monkeypatch ``get_scheduler`` to return a
Memory-backed AsyncIOScheduler and assert on the shape of what schedule(),
list_jobs(), cancel(), and run_now() do.

The tests skip cleanly when APScheduler isn't installed so a barebones CI
image still passes.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

apscheduler = pytest.importorskip("apscheduler")  # noqa: F841

from companybrain.harness import scheduler as scheduler_mod
from apscheduler.schedulers.asyncio import AsyncIOScheduler  # type: ignore[import-not-found]


@pytest.fixture
async def memory_scheduler(monkeypatch):
    """Replace the singleton with a memory-backed scheduler.

    AsyncIOScheduler.start() requires a running event loop, so this is an
    async fixture — pytest-asyncio runs it inside the test's loop.
    """
    sched = AsyncIOScheduler()  # default jobstore: memory
    sched.start(paused=False)
    monkeypatch.setattr(scheduler_mod, "_scheduler", sched, raising=False)
    yield sched
    try:
        sched.shutdown(wait=False)
    except Exception:
        pass
    monkeypatch.setattr(scheduler_mod, "_scheduler", None, raising=False)


@pytest.fixture
def stub_runner(monkeypatch):
    """Capture invocations so tests can assert run_now actually fires the body."""
    calls: list[dict[str, Any]] = []

    async def runner(**kwargs):
        calls.append(kwargs)
        return {"ok": True, "kwargs": kwargs}

    scheduler_mod.configure_runner(runner)
    yield calls
    scheduler_mod.configure_runner(scheduler_mod._default_runner)


# ── build_jobstore_url ───────────────────────────────────────────────────────

def test_build_jobstore_url_strips_asyncpg(monkeypatch):
    """APScheduler runs sync — the +asyncpg dialect must be stripped."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@h/db")
    assert scheduler_mod.build_jobstore_url() == "postgresql://u:p@h/db"


def test_build_jobstore_url_default(monkeypatch):
    """Default URL is stable so tests can rely on it."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert scheduler_mod.build_jobstore_url().startswith("postgresql://")


# ── schedule + list + cancel ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_schedule_persists_a_job(memory_scheduler, stub_runner):
    job_id = await scheduler_mod.schedule(
        name="t1",
        repo="/tmp/repo",
        endpoint="/api/x",
        method="GET",
        cron="* * * * *",
        workspace_id="ws-uuid",
    )
    assert job_id == "t1"

    jobs = scheduler_mod.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].id == "t1"
    assert jobs[0].repo == "/tmp/repo"
    assert jobs[0].endpoint == "/api/x"
    assert jobs[0].method == "GET"
    assert jobs[0].workspace_id == "ws-uuid"


@pytest.mark.asyncio
async def test_cancel_removes_job(memory_scheduler, stub_runner):
    await scheduler_mod.schedule(
        name="t2", repo="/tmp/r", endpoint="/api/y",
        method="POST", cron="0 0 * * *",
    )
    assert scheduler_mod.cancel("t2") is True
    assert scheduler_mod.list_jobs() == []
    # Cancelling a missing job is False, not an exception.
    assert scheduler_mod.cancel("does-not-exist") is False


@pytest.mark.asyncio
async def test_run_now_fires_runner_with_kwargs(memory_scheduler, stub_runner):
    await scheduler_mod.schedule(
        name="t3", repo="/repo", endpoint="/api/z", method="DELETE",
        cron="0 12 * * *", workspace_id="ws-3", extra={"branch": "develop"},
    )

    outcome = await scheduler_mod.run_now("t3")

    assert outcome["ok"] is True
    assert len(stub_runner) == 1
    fired = stub_runner[0]
    assert fired["repo"] == "/repo"
    assert fired["endpoint"] == "/api/z"
    assert fired["method"] == "DELETE"
    assert fired["workspace_id"] == "ws-3"
    assert fired["branch"] == "develop"


@pytest.mark.asyncio
async def test_run_now_returns_error_for_unknown_job(memory_scheduler, stub_runner):
    outcome = await scheduler_mod.run_now("never-scheduled")
    assert outcome["ok"] is False
    assert "unknown job" in outcome["error"]


@pytest.mark.asyncio
async def test_replace_existing_overwrites_first_definition(
    memory_scheduler, stub_runner,
):
    await scheduler_mod.schedule(
        name="t4", repo="/r1", endpoint="/api/a",
        method="GET", cron="* * * * *",
    )
    await scheduler_mod.schedule(
        name="t4", repo="/r2", endpoint="/api/b",
        method="POST", cron="0 0 * * *",
    )

    jobs = scheduler_mod.list_jobs()
    assert len(jobs) == 1
    assert jobs[0].repo == "/r2"
    assert jobs[0].endpoint == "/api/b"
