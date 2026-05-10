"""Acceptance tests for ADR-0052 Phase 5 — slash + MCP + workspace + rooms + headless + SDK.

Each test asserts one headline P5 property end-to-end against deterministic
fixtures (no live LLM, no live network). The combined suite is the gate the
PR must pass before merge.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from companybrain.harness.commands import (
    SlashCommandError,
    load_default_commands,
    parse_and_render,
)
from companybrain.harness.mcp_server import build_server
from companybrain.harness.worktree import WorktreeManager
from companybrain.harness.workspace import Workspace
from companybrain.store.base import BrainEntity
from companybrain.store.json_store import JsonFileBrainStore


# ── 1. Slash commands route to the right handler ──────────────────────────


def test_slash_commands_route_correctly():
    """All 10 slash commands resolve via parse_and_render to their declared name."""
    registry = load_default_commands()
    assert set(registry.names) == {
        "extract", "query", "verify", "diff", "cost",
        "explain", "wipe", "stats", "init", "skills",
    }
    # Each command must render with at least its required args populated.
    for cmd in registry.all():
        # Build a minimal arg string: required args become placeholders.
        argv = " ".join(_dummy_value(a) for a in cmd.args if a.required)
        rendered, name = parse_and_render(f"/{cmd.name} {argv}".rstrip())
        assert name == cmd.name
        assert rendered  # not empty


def _dummy_value(arg) -> str:
    return {
        "string":  "x",
        "integer": "1",
    }.get(arg.type, "x")


def test_unknown_slash_command_raises():
    with pytest.raises(SlashCommandError):
        parse_and_render("/notarealcommand foo")


# ── 2. MCP server responds to external query ──────────────────────────────


async def test_mcp_server_responds_to_external_query(tmp_path: Path):
    """Build a server, call query_brain via the JSON-RPC bridge, see seeded entity."""
    store = JsonFileBrainStore(tmp_path / ".brain")
    await store.write(
        BrainEntity(
            id="urn:cb:dev:code:demo:method:Foo.bar",
            entity_type="function_node",
            repo="demo",
            file="src/Foo.java",
            qualified_name="Foo.bar",
            t1_summary="Looks up competitive_payer_plan rows for getPayerCompetitors.",
        ),
        run_id="r1",
        workspace_id="ws-test",
    )

    server = build_server(workspace_id="ws-test", brain_root=tmp_path)
    client = TestClient(server.build_asgi_app())

    list_resp = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/list",
    })
    names = {t["name"] for t in list_resp.json()["result"]["tools"]}
    assert "query_brain" in names

    call_resp = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": "query_brain", "arguments": {
            "question": "what tables does getPayerCompetitors read?",
        }},
    })
    body = call_resp.json()["result"]
    assert body["isError"] is False
    inner = json.loads(body["content"][0]["text"])
    matches = inner["matches"]
    assert any("competitive_payer_plan" in (m.get("summary") or "") for m in matches)


# ── 3. Concurrent worktrees stay isolated ─────────────────────────────────


async def test_concurrent_worktrees_isolated(tmp_path: Path):
    """Two coroutines pinned to different commits don't see each other's files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    sha_a, sha_b = _git_init_with_two_commits(repo)

    async def _check(commit: str, expected: str, missing: str) -> bool:
        async with WorktreeManager(repo, commit_sha=commit) as wt:
            await asyncio.sleep(0.05)
            return (wt / expected).exists() and not (wt / missing).exists()

    a, b = await asyncio.gather(
        _check(sha_a, "a.txt", "b.txt"),
        _check(sha_b, "b.txt", "missing.txt"),
    )
    assert a and b


# ── 4. git_branch_diff produces a tight file list ─────────────────────────


async def test_branch_diff_returns_changed_files(tmp_path: Path):
    """git_branch_diff reports only the files changed between two refs."""
    from companybrain.harness.tools import TOOL_REGISTRY

    repo = tmp_path / "repo"
    repo.mkdir()
    _, sha_b = _git_init_with_two_commits(repo)
    # Create a feature branch with one extra file changed.
    _git(repo, "checkout", "-q", "-b", "feature/x")
    (repo / "a.txt").write_text("alpha-changed\n")
    (repo / "c.txt").write_text("charlie\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "feature change")

    result = await TOOL_REGISTRY["git_branch_diff"].invoke(
        {"branch_a": "main", "branch_b": "feature/x", "extensions": []},
        context={"repo_path": str(repo)},
    )
    assert result["ok"] is True
    files = set(result["files"])
    assert {"a.txt", "c.txt"}.issubset(files)
    # The unchanged b.txt must NOT appear.
    assert "b.txt" not in files


# ── 5. Headless --json emits a structured payload ─────────────────────────


def test_headless_json_payload_is_pipeable(tmp_path: Path):
    """The headless runner returns parseable JSON with telemetry. (Direct call,
    no orchestrator — keeps the test free of LLM/db dependencies.)"""
    from companybrain.cli_helpers import headless

    # Empty repo → discover_endpoints returns []. Dry-run path bails before
    # touching the orchestrator, so no DB/LLM is needed.
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# placeholder")

    with patch("companybrain.cli_helpers.headless.run_structural_prepass") as p_prepass:
        async def fake_prepass(**_kw):
            return _FakePrepass()
        p_prepass.side_effect = fake_prepass
        with patch("companybrain.cli_helpers.headless.discover_endpoints", return_value=[]):
            payload, exit_code = asyncio.run(headless.run_index_headless(
                repo_path=repo,
                branch="main",
                workspace_id="ws-test",
                endpoints=None,
                repo_name="demo",
                dry_run=True,
            ))

    assert exit_code == 0
    # Must round-trip through json.dumps without raising — that's what `--json` does.
    serialised = json.dumps(payload, default=str)
    parsed = json.loads(serialised)
    assert parsed["ok"] is True
    assert "telemetry" in parsed
    assert "wall_time_seconds" in parsed["telemetry"]


# ── 6. Workspace settings hierarchy resolves correctly ────────────────────


def test_workspace_settings_hierarchy_picks_repo_settings(tmp_path: Path):
    """Repo-level .brain/settings.json wins over the user-level default."""
    (tmp_path / ".brain").mkdir()
    (tmp_path / ".brain" / "settings.json").write_text(json.dumps({
        "workspace_id":   "from-repo",
        "workspace_slug": "stage",
    }))
    ws = Workspace.load(tmp_path)
    assert ws.id == "from-repo"
    assert ws.slug == "stage"


# ── helpers ────────────────────────────────────────────────────────────────


def _git_init_with_two_commits(repo: Path) -> tuple[str, str]:
    _git(repo, "init", "-q", "-b", "main")
    (repo / "a.txt").write_text("alpha\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "first")
    sha_a = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
    (repo / "b.txt").write_text("bravo\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "second")
    sha_b = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"]).decode().strip()
    return sha_a, sha_b


def _git(repo: Path, *args: str) -> None:
    env = {**os.environ,
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
           "GIT_AUTHOR_NAME": "t",    "GIT_AUTHOR_EMAIL": "t@t"}
    subprocess.run(["git", "-C", str(repo), *args], check=True, env=env)


class _FakePrepass:
    fresh_units: list = []
    dirty_units: list = []
    cb_api_status: str = "ok"
