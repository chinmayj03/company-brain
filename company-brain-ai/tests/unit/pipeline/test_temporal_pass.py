"""Unit tests for ADR-0059 Pass T1 — temporal_pass + git_blame_aggregator
+ risk_alert_detector + onboarding_path_builder.

These tests exercise the deterministic logic without standing up a real git
repository: we monkey-patch the aggregator's `blame_file`/`file_commits`
to return canned data, then assert the pass populates ``entity.temporal``
and that the detector emits the right alerts.

A separate small slice does exercise the subprocess git path against a
real temp repo so we get coverage of the porcelain parser.
"""
from __future__ import annotations

import os
import subprocess
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import pytest

from companybrain.models.entities import (
    DomainEntity,
    ExtractedEntity,
    OnboardingPath,
    RiskAlert,
    TemporalOwnership,
)
from companybrain.pipeline import git_blame_aggregator as _blame
from companybrain.pipeline.onboarding_path_builder import (
    build_onboarding_paths,
)
from companybrain.pipeline.risk_alert_detector import detect_risk_alerts
from companybrain.pipeline.temporal_pass import run_temporal_pass


# ── Factories ─────────────────────────────────────────────────────────────────

def _entity(name: str, file: str = "java/X.java", etype: str = "Class") -> ExtractedEntity:
    return ExtractedEntity(
        entity_type=etype, name=name, file=file, repo="repo",
        signature=f"class {name}",
        last_modified_commit="abc",
        confidence=0.9,
    )


def _blame_line(line_no: int, author: str, sha: str = "deadbeef" * 5,
                ts: Optional[datetime] = None) -> _blame.BlameLine:
    return _blame.BlameLine(
        line_no=line_no, author=author, author_mail=author.lower() + "@x",
        commit_sha=sha,
        commit_time=ts or datetime(2025, 1, 1, tzinfo=timezone.utc),
    )


def _touch(sha: str, author: str, days_ago: int) -> _blame.CommitTouch:
    ts = datetime.now(tz=timezone.utc) - timedelta(days=days_ago)
    return _blame.CommitTouch(
        sha=sha, author=author, author_mail=author.lower() + "@x", timestamp=ts,
    )


# ── temporal_pass ─────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _clear_blame_cache():
    _blame.clear_cache()
    yield
    _blame.clear_cache()


async def test_temporal_pass_empty_input_is_noop():
    entities, stats = await run_temporal_pass(
        [], repo_resolver=lambda _: Path("/tmp/no-such"),
    )
    assert entities == []
    assert stats.entities_seen == 0


async def test_temporal_pass_skips_ineligible_entity_types(monkeypatch):
    pattern = _entity("MyPattern", etype="Pattern")
    domain  = _entity("Payer", etype="DomainEntity")
    method  = _entity("doX",   etype="Method")
    monkeypatch.setattr(_blame, "blame_file",  lambda *a, **k: [_blame_line(1, "Sarah")])
    monkeypatch.setattr(_blame, "file_commits", lambda *a, **k: [_touch("s1", "Sarah", days_ago=10)])
    out, stats = await run_temporal_pass(
        [pattern, domain, method],
        repo_resolver=lambda _: Path("/tmp/x"),
    )
    assert pattern.temporal is None and domain.temporal is None
    assert method.temporal is not None
    assert stats.entities_blamed == 1


async def test_temporal_pass_populates_primary_author_and_bus_factor(monkeypatch):
    e = _entity("CompetitivenessPlanRepository")
    monkeypatch.setattr(_blame, "blame_file", lambda *a, **k: [
        *(_blame_line(i, "Sarah") for i in range(1, 81)),
        *(_blame_line(i, "Bob")    for i in range(81, 90)),
        *(_blame_line(i, "Alex")   for i in range(90, 101)),
    ])
    monkeypatch.setattr(_blame, "file_commits", lambda *a, **k: [
        _touch("s1", "Sarah", days_ago=2),
        _touch("s2", "Sarah", days_ago=15),
        _touch("b1", "Bob",   days_ago=200),
    ])
    [out], stats = await run_temporal_pass(
        [e], repo_resolver=lambda _: Path("/tmp/x"),
    )
    assert out.temporal is not None
    t = out.temporal
    assert t.primary_author == "Sarah"
    assert t.co_authors[0] == ("Sarah", 80)
    assert t.bus_factor == 2          # Sarah (80%) + Alex (11%); Bob (9%) < 10%
    assert t.last_touched_by == "Sarah"
    assert t.churn_30d == 2
    assert t.churn_90d == 2


async def test_temporal_pass_missing_repo_root_is_skipped(monkeypatch):
    e = _entity("X")
    monkeypatch.setattr(_blame, "blame_file",  lambda *a, **k: [_blame_line(1, "Sarah")])
    monkeypatch.setattr(_blame, "file_commits", lambda *a, **k: [_touch("c1", "Sarah", 1)])
    [out], stats = await run_temporal_pass(
        [e], repo_resolver=lambda _: None,
    )
    assert out.temporal is None
    assert stats.entities_skipped == 1


# ── git_blame_aggregator (subprocess path) ────────────────────────────────────

def _git(repo: Path, *args: str, **env_extras) -> None:
    env = {**os.environ, "GIT_AUTHOR_DATE": "2025-01-01T00:00:00",
           "GIT_COMMITTER_DATE": "2025-01-01T00:00:00", **env_extras}
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   capture_output=True, env=env)


def test_blame_file_returns_lines_from_real_repo(tmp_path: Path):
    """Smoke test the subprocess git fallback against a real one-file repo."""
    repo = tmp_path / "demo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "sarah@example.com")
    _git(repo, "config", "user.name", "Sarah")
    f = repo / "src.txt"
    f.write_text("line one\nline two\n")
    _git(repo, "add", "src.txt")
    _git(repo, "commit", "-m", "init", "--allow-empty-message")

    lines = _blame.blame_file(repo, "src.txt")
    assert len(lines) == 2
    assert all("sarah" in l.author_mail or l.author == "Sarah" for l in lines)


# ── risk_alert_detector ───────────────────────────────────────────────────────

def test_bus_factor_one_emitted_when_primary_above_70_and_runner_below_10():
    e = _entity("CompetitivenessPlanRepository")
    e.temporal = TemporalOwnership(
        primary_author="Sarah",
        co_authors=[("Sarah", 85), ("Bob", 8), ("Alex", 7)],
        bus_factor=1,
        last_touched_by="Sarah",
        age_days=120,
        churn_30d=0, churn_90d=2,
    )
    alerts, edges = detect_risk_alerts([e])
    bus = [a for a in alerts if a.kind == "bus_factor_one"]
    assert len(bus) == 1
    assert "Sarah" in bus[0].message
    assert "85%" in bus[0].message
    assert edges and edges[0].edge_type == "AFFECTS"


def test_bus_factor_one_skipped_when_runner_above_10():
    e = _entity("BalancedFile")
    e.temporal = TemporalOwnership(
        primary_author="Sarah",
        co_authors=[("Sarah", 75), ("Bob", 20), ("Alex", 5)],
        bus_factor=2,
        last_touched_by="Bob",
        age_days=120,
    )
    alerts, _ = detect_risk_alerts([e])
    assert not [a for a in alerts if a.kind == "bus_factor_one"]


def test_high_churn_alert_when_churn30_above_threshold():
    e = _entity("RecentlyShuffled")
    e.temporal = TemporalOwnership(
        primary_author="Sarah",
        co_authors=[("Sarah", 40), ("Bob", 30), ("Alex", 30)],
        bus_factor=3,
        last_touched_by="Alex",
        age_days=400,
        churn_30d=7, churn_90d=12,
    )
    alerts, _ = detect_risk_alerts([e])
    churn = [a for a in alerts if a.kind == "high_churn"]
    assert len(churn) == 1
    assert "7" in churn[0].message


def test_stale_owner_left_alert_when_lookup_says_silent_90d():
    e = _entity("AbandonedFile")
    e.temporal = TemporalOwnership(
        primary_author="Sarah",
        co_authors=[("Sarah", 60), ("Bob", 25), ("Alex", 15)],
        bus_factor=3,
        last_touched_by="Sarah",
        age_days=400,
        churn_30d=0, churn_90d=1,
    )
    now = datetime.now(tz=timezone.utc)
    long_ago = now - timedelta(days=120)
    alerts, _ = detect_risk_alerts(
        [e], author_last_seen_lookup=lambda _: long_ago, now=now,
    )
    stale = [a for a in alerts if a.kind == "stale_owner_left"]
    assert len(stale) == 1
    assert "120" in stale[0].message


def test_no_alerts_when_temporal_missing():
    e = _entity("NoBlame")
    alerts, edges = detect_risk_alerts([e])
    assert alerts == [] and edges == []


# ── onboarding_path_builder ───────────────────────────────────────────────────

def test_onboarding_picks_controller_service_repository_in_order():
    classes = [
        _entity("CompetitivenessController"),
        _entity("CompetitivenessService"),
        _entity("CompetitivenessPlanRepository"),
        _entity("CompetitivenessDto"),
        _entity("CompetitivenessSummary"),
    ]
    domain = DomainEntity(
        name="Competitiveness",
        anchor_class_urns=[c.external_id for c in classes],
        description="Competitive analysis features",
        confidence=0.8,
    )
    result = build_onboarding_paths([domain], classes, max_anchors_per_path=5)
    assert len(result.paths) == 1
    path = result.paths[0]
    names = [u.split("::")[-1] for u in path.anchor_class_urns]
    # Top-of-stack first.
    assert names.index("CompetitivenessController") < names.index("CompetitivenessService")
    assert names.index("CompetitivenessService") < names.index("CompetitivenessPlanRepository")


def test_onboarding_skips_domains_with_no_anchors():
    domain = DomainEntity(name="Empty", anchor_class_urns=["bogus"], confidence=0.5)
    result = build_onboarding_paths([domain], entities=[])
    assert result.paths == []


def test_onboarding_emits_guides_and_read_first_edges():
    e = _entity("FooController")
    domain = DomainEntity(name="Foo", anchor_class_urns=[e.external_id], confidence=0.5)
    result = build_onboarding_paths([domain], [e])
    assert len(result.paths) == 1
    edge_kinds = {edge.edge_type for edge in result.edges}
    assert "GUIDES" in edge_kinds
    assert "READ_FIRST" in edge_kinds
