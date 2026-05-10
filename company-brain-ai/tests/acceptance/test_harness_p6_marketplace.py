"""Acceptance tests for ADR-0052 Phase 6 — marketplace + scheduled + notebook + image + verifier + notes.

Each test asserts one headline P6 property end-to-end against deterministic
fixtures. We don't stand up a live Postgres, Anthropic vision API, or a real
Chromium browser — those are exercised in integration. Instead the suite
proves the wiring: the fixture plugin overrides the bundled spring-boot
skill, the scheduler persists/cancels/runs jobs, notebook cells flow through
``CodeChunker``, vision-extracted artifacts are well-formed, browser drift is
calculated correctly, and pinned/proposed flags survive the model layer.
"""
from __future__ import annotations

import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from companybrain.harness import notes as notes_mod
from companybrain.harness import plugins as plugins_mod
from companybrain.harness import scheduler as scheduler_mod
from companybrain.harness import skills
from companybrain.harness.notebook_chunker import chunk_notebook
from companybrain.harness.subagents import browser_verifier
from companybrain.models.entities import ExtractedEntity


_FIXTURE_BUNDLE = (
    Path(__file__).resolve().parent.parent.parent
    / "fixtures" / "plugins" / "acme-spring-boot.zip"
)


# ── 1. Plugin marketplace overrides bundled skills ───────────────────────────

def test_plugin_install_overrides_bundled_skill(tmp_path, monkeypatch):
    """Installing acme-spring-boot makes ``load_skill('spring-boot')`` return
    the plugin's SKILL.md, not the bundled framework version.

    The bundle ships at ``fixtures/plugins/acme-spring-boot.zip``; the unit
    test in test_plugins.py covers the build-from-scratch case but this test
    asserts the canonical file actually round-trips.
    """
    monkeypatch.setenv("BRAIN_PLUGIN_HOME", str(tmp_path / "plugin_home"))
    plugin = plugins_mod.install(str(_FIXTURE_BUNDLE))
    assert plugin.name == "acme-spring-boot"

    text = skills.load_skill("spring-boot")
    assert "Acme Spring Boot conventions" in text
    assert "@AcmeAuditable" in text
    # And without the plugin installed, we fall back to the bundled tree.
    plugins_mod.uninstall("acme-spring-boot")
    bundled = skills.load_skill("spring-boot")
    assert "Acme Spring Boot conventions" not in bundled


# ── 2. Scheduler persists, lists, cancels, runs ──────────────────────────────


@pytest.fixture
async def memory_scheduler(monkeypatch):
    """Memory-backed AsyncIOScheduler — no Postgres required for this acceptance.

    Async fixture because AsyncIOScheduler.start() needs a running loop.
    """
    pytest.importorskip("apscheduler")
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    sched = AsyncIOScheduler()
    sched.start(paused=False)
    monkeypatch.setattr(scheduler_mod, "_scheduler", sched, raising=False)
    yield sched
    try:
        sched.shutdown(wait=False)
    except Exception:
        pass
    monkeypatch.setattr(scheduler_mod, "_scheduler", None, raising=False)


@pytest.mark.asyncio
async def test_scheduled_task_persists_and_runs(memory_scheduler):
    """schedule → list_jobs → run_now → cancel round-trip."""
    fired: list[dict] = []

    async def stub(**kwargs):
        fired.append(kwargs)
        return {"ok": True, "kwargs": kwargs}

    scheduler_mod.configure_runner(stub)
    try:
        job_id = await scheduler_mod.schedule(
            name="rebuild",
            repo="/tmp/repo", endpoint="/api/x",
            method="GET", cron="* * * * *",
            workspace_id="ws-1",
        )
        assert job_id == "rebuild"

        jobs = scheduler_mod.list_jobs()
        assert [j.id for j in jobs] == ["rebuild"]

        outcome = await scheduler_mod.run_now("rebuild")
        assert outcome["ok"] is True
        assert fired and fired[0]["repo"] == "/tmp/repo"

        assert scheduler_mod.cancel("rebuild") is True
        assert scheduler_mod.list_jobs() == []
    finally:
        scheduler_mod.configure_runner(scheduler_mod._default_runner)


# ── 3. Notebook cells flow through chunking ──────────────────────────────────


def test_notebook_extracts_cells(tmp_path: Path):
    """Three code cells produce three chunks with stable cell ordering."""
    nb = tmp_path / "ml-pipeline.ipynb"
    nb.write_text(json.dumps({
        "metadata": {"language_info": {"name": "python"}},
        "nbformat": 4, "nbformat_minor": 5,
        "cells": [
            {"cell_type": "code", "source": "import pandas as pd\n",
             "metadata": {}, "outputs": [], "execution_count": 1},
            {"cell_type": "code", "source": "df = pd.read_csv('x.csv')\n",
             "metadata": {}, "outputs": [], "execution_count": 2},
            {"cell_type": "code", "source": "df.head()\n",
             "metadata": {}, "outputs": [], "execution_count": 3},
        ],
    }))

    chunks = chunk_notebook(nb)
    assert len(chunks) == 3
    assert all(c.language == "python" for c in chunks)
    assert [c.qname for c in chunks] == [
        "ml-pipeline.cell_0", "ml-pipeline.cell_1", "ml-pipeline.cell_2",
    ]


# ── 4. Diagram extractor produces a well-formed Artifact ─────────────────────


@pytest.mark.asyncio
async def test_diagram_extracted_as_artifact(monkeypatch, tmp_path: Path):
    """A monkeypatched vision provider yields one diagram Artifact per image."""
    from companybrain.harness import image_extractor as ie

    docs = tmp_path / "docs" / "arch"
    docs.mkdir(parents=True)
    (docs / "auth-flow.png").write_bytes(b"\x89PNG\r\n\x1a\nstub")

    class _Block:
        def __init__(self, text): self.text = text
    class _Messages:
        async def create(self, **kwargs):
            msg = type("M", (), {})()
            msg.content = [_Block(
                '{"components": [{"name":"Auth","kind":"service"},'
                '{"name":"Sessions","kind":"database"}],'
                '"edges":[{"from":"Auth","to":"Sessions","label":"writes"}]}'
            )]
            return msg
    class _Client:
        messages = _Messages()
    class _Provider:
        provider_name = "fake"
        _client = _Client()
        def model_for_role(self, role): return "fake-model"

    monkeypatch.setattr(ie, "get_provider", lambda: _Provider())

    artifacts = await ie.extract_repo_diagrams(tmp_path)
    diagrams = [a for a in artifacts if a.kind == "diagram"]
    assert len(diagrams) == 1
    art = diagrams[0]
    assert art.metadata["components_count"] == 2
    assert art.metadata["edges_count"] == 1


# ── 5. Browser verifier surfaces drift ───────────────────────────────────────


@pytest.mark.asyncio
async def test_browser_verifier_reports_drift_against_brain():
    """Frontend calls /api/users; brain only knows /api/orders → drift fired."""
    observed = [
        "http://app/api/users/42",
        "http://app/api/orders/7",
        "http://app/static/main.js",     # filtered as static asset
    ]
    result = await browser_verifier.verify(
        frontend_url="ignored-when-stubbed",
        brain_endpoints=["/api/orders/{id}"],
        extra_observations=observed,
    )
    assert result.backend == "stub"
    drift_urls = [d.observed_url for d in result.drift]
    assert "http://app/api/users/42" in drift_urls
    # /api/orders/{id} matches /api/orders/7 once braces are stripped — no drift.
    assert "http://app/api/orders/7" not in drift_urls
    # Static asset must not show up as drift.
    assert "http://app/static/main.js" not in drift_urls


# ── 6. Notes round-trip via render_for_query ─────────────────────────────────


def test_notes_render_attaches_authorship_when_present():
    """``render_for_query`` keeps the wire shape minimal but preserves authors."""
    rows = [
        notes_mod.EntityNote(
            id=1, workspace_id="ws", entity_urn="urn:cb:dev:code:r:m:Foo.bar",
            note="Deprecated 2026-Q4",
            author="alice",
            created_at=datetime(2026, 4, 1, tzinfo=timezone.utc),
        ),
    ]
    rendered = notes_mod.render_for_query(rows)
    assert rendered == [{
        "urn":  "urn:cb:dev:code:r:m:Foo.bar",
        "note": "Deprecated 2026-Q4",
        "author": "alice",
        "created_at": "2026-04-01T00:00:00+00:00",
    }]


# ── 7. Pinned / proposed flags survive the model layer ──────────────────────


def test_pinned_entity_carries_flag_through_model():
    """ExtractedEntity defaults pinned/proposed to False and round-trips them.

    The acceptance for "pinned entity not overwritten" lives in the
    integration suite where a real DB exists; here we just assert that the
    extraction model surface carries the flags so the writer can read them.
    """
    e = ExtractedEntity(
        entity_type="function_node",
        name="Foo.bar",
        file="src/Foo.java",
        repo="demo",
        signature="public void bar()",
        last_modified_commit="abc123",
        confidence=0.9,
    )
    assert e.pinned is False
    assert e.proposed is False

    pinned = ExtractedEntity(
        entity_type="function_node",
        name="Foo.frozen",
        file="src/Foo.java",
        repo="demo",
        signature="public void frozen()",
        last_modified_commit="def456",
        confidence=0.9,
        pinned=True,
        proposed=True,
    )
    assert pinned.pinned is True
    assert pinned.proposed is True
