"""Unit tests for the Workspace dataclass + settings hierarchy (ADR-0052 P5)."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from companybrain.harness.permissions import DEFAULT_GRANTS, Decision, Capability
from companybrain.harness.workspace import Workspace


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                     "commit", "-q", "--allow-empty", "-m", "init"],
                    cwd=repo, check=True)


def test_load_with_defaults_returns_dev_workspace(tmp_path: Path):
    """A bare directory yields the dev workspace + DEFAULT_GRANTS."""
    ws = Workspace.load(tmp_path)
    assert ws.id == "00000000-0000-0000-0000-000000000001"
    assert ws.slug == "dev"
    assert ws.repo_path == tmp_path.resolve()
    assert ws.branch == "main"
    # Without a git checkout, commit_sha is None.
    assert ws.commit_sha is None
    # capabilities default to DEFAULT_GRANTS
    assert ws.capabilities.granted == DEFAULT_GRANTS.granted


def test_load_resolves_commit_sha_from_git(tmp_path: Path):
    """When repo_path is a git checkout, commit_sha is captured."""
    _git_init(tmp_path)
    ws = Workspace.load(tmp_path)
    assert ws.commit_sha and len(ws.commit_sha) == 40


def test_repo_settings_override_defaults(tmp_path: Path):
    """A .brain/settings.json on disk wins over the hard-coded defaults."""
    (tmp_path / ".brain").mkdir()
    (tmp_path / ".brain" / "settings.json").write_text(json.dumps({
        "workspace_id":   "deadbeef-0000-0000-0000-000000000000",
        "workspace_slug": "stage",
        "branch":         "develop",
    }))
    ws = Workspace.load(tmp_path)
    assert ws.id == "deadbeef-0000-0000-0000-000000000000"
    assert ws.slug == "stage"
    assert ws.branch == "develop"


def test_explicit_args_beat_settings(tmp_path: Path):
    """Caller-supplied workspace_id wins over the settings file."""
    (tmp_path / ".brain").mkdir()
    (tmp_path / ".brain" / "settings.json").write_text(
        json.dumps({"workspace_id": "from-settings"})
    )
    ws = Workspace.load(tmp_path, workspace_id="cli-override")
    assert ws.id == "cli-override"


def test_repo_settings_grants_propagate(tmp_path: Path):
    """Settings's grants block becomes the WorkspaceGrants overrides."""
    (tmp_path / ".brain").mkdir()
    (tmp_path / ".brain" / "settings.json").write_text(json.dumps({
        "grants": {"exec_shell": "auto"},
    }))
    ws = Workspace.load(tmp_path)
    assert ws.capabilities.for_capability(Capability.EXEC_SHELL) == Decision.AUTO


def test_to_context_emits_legacy_keys(tmp_path: Path):
    """to_context() returns the same keys today's tools read from `context`."""
    ws = Workspace.load(tmp_path)
    ctx = ws.to_context()
    assert ctx["workspace_id"] == ws.id
    assert ctx["workspace_slug"] == ws.slug
    assert ctx["repo_path"] == str(ws.repo_path)
    assert ctx["branch"] == ws.branch
    # workspace itself is exposed for the new code paths.
    assert ctx["workspace"] is ws


def test_replace_returns_new_instance(tmp_path: Path):
    ws = Workspace.load(tmp_path)
    ws2 = ws.replace(branch="feature/x")
    assert ws is not ws2
    assert ws.branch == "main"
    assert ws2.branch == "feature/x"


def test_load_handles_invalid_json_gracefully(tmp_path: Path):
    """A malformed settings file falls back to defaults rather than raising."""
    (tmp_path / ".brain").mkdir()
    (tmp_path / ".brain" / "settings.json").write_text("not-json{")
    ws = Workspace.load(tmp_path)
    assert ws.slug == "dev"  # falls back to default


def test_workspace_is_frozen(tmp_path: Path):
    """The dataclass is frozen so accidental mutation raises."""
    ws = Workspace.load(tmp_path)
    with pytest.raises(Exception):  # FrozenInstanceError
        ws.id = "mutated"  # type: ignore[misc]
