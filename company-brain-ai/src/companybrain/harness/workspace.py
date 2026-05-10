"""Workspace — single source of truth for repo + workspace + capabilities (ADR-0052 P5).

Until P5, workspace metadata was scattered across env vars (``BRAIN_WORKSPACE_ID``,
``BRAIN_WORKSPACE_SLUG``), function arguments, and ad-hoc lookups in
:mod:`companybrain.cli`. The :class:`Workspace` dataclass consolidates that into
one immutable bag of "everything a tool dispatch needs to know about the run":
the repo path, the branch and commit, the workspace UUID + slug, the loaded
capability grants, and a handful of env-var overrides.

Settings hierarchy (later wins, deep-merged):

  1. Hard-coded defaults in this module.
  2. ``~/.brain/settings.json``  — per-user defaults.
  3. ``<repo>/.brain/settings.json``  — per-repo overrides.
  4. ``BRAIN_ENTERPRISE_CONFIG_URL`` — optional org-wide JSON document fetched
     once at load time. Failures are logged and ignored — settings must keep
     working when the org config server is unreachable.

The Workspace is intentionally *frozen*: once you've loaded it, every tool sees
the same view. Mutation is a flag for a bug — clone with :meth:`replace`.
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from dataclasses import replace as _dataclass_replace
from pathlib import Path
from typing import Any

import structlog

from companybrain.harness.permissions import (
    DEFAULT_GRANTS,
    WorkspaceGrants,
    load_workspace_grants,
)

log = structlog.get_logger(__name__)


_DEFAULT_WORKSPACE_ID  = "00000000-0000-0000-0000-000000000001"
_DEFAULT_WORKSPACE_SLUG = "dev"


@dataclass(frozen=True)
class Workspace:
    """One workspace's resolved view of the repo + capabilities + env.

    Pass this around in ``context["workspace"]`` so tools never reach for
    ``os.environ`` or recompute ``git rev-parse HEAD``.
    """

    id: str
    slug: str
    repo_path: Path
    branch: str = "main"
    commit_sha: str | None = None
    capabilities: WorkspaceGrants = field(default_factory=lambda: DEFAULT_GRANTS)
    settings: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)

    # ── factory helpers ────────────────────────────────────────────────────

    @classmethod
    def load(
        cls,
        repo_path: Path | str,
        *,
        workspace_id: str | None = None,
        workspace_slug: str | None = None,
        branch: str | None = None,
        commit_sha: str | None = None,
    ) -> Workspace:
        """Resolve a Workspace from the settings hierarchy.

        Caller-supplied arguments win over the merged settings — that lets the
        CLI plumb ``--workspace`` / ``--branch`` flags through without each
        flag having to reach into the settings layer.
        """
        repo = Path(repo_path).resolve()
        merged = _load_settings_hierarchy(repo)

        ws_id = (workspace_id or merged.get("workspace_id")
                  or os.environ.get("BRAIN_WORKSPACE_ID")
                  or _DEFAULT_WORKSPACE_ID)
        ws_slug = (workspace_slug or merged.get("workspace_slug")
                    or os.environ.get("BRAIN_WORKSPACE_SLUG")
                    or _DEFAULT_WORKSPACE_SLUG)
        branch_resolved = (branch or merged.get("branch")
                           or os.environ.get("BRAIN_BRANCH") or "main")
        commit = commit_sha or _resolve_commit_sha(repo)

        # Capabilities can be set globally (BRAIN_GRANTS), per-repo (settings),
        # or left at DEFAULT_GRANTS. Per-repo wins because operators usually
        # tighten or loosen on a project-by-project basis.
        grants_overrides = dict(merged.get("grants") or {})
        grants = load_workspace_grants(ws_id, overrides=grants_overrides)

        env_overrides = dict(merged.get("env") or {})

        return cls(
            id=ws_id,
            slug=ws_slug,
            repo_path=repo,
            branch=branch_resolved,
            commit_sha=commit,
            capabilities=grants,
            settings=merged,
            env=env_overrides,
        )

    # ── ergonomics ─────────────────────────────────────────────────────────

    def replace(self, **changes: Any) -> Workspace:
        """Return a copy with one or more fields swapped."""
        return _dataclass_replace(self, **changes)

    def to_context(self) -> dict[str, Any]:
        """Render the workspace as the keys the existing tools expect.

        Existing tool handlers read ``context["repo_path"]``,
        ``context["workspace_id"]``, etc. Returning the same shape lets P5
        replace the per-call dict construction with one workspace object
        without touching every tool.
        """
        return {
            "workspace":       self,
            "workspace_id":    self.id,
            "workspace_slug":  self.slug,
            "repo_path":       str(self.repo_path),
            "branch":          self.branch,
            "commit_sha":      self.commit_sha or "",
        }


# ── settings hierarchy ──────────────────────────────────────────────────────


def _load_settings_hierarchy(repo: Path) -> dict[str, Any]:
    """Resolve the settings dict from user → repo → enterprise.

    Each layer is a JSON object; we deep-merge so later layers can override
    individual leaves without restating the whole document. Missing files
    contribute ``{}`` — operators don't have to create every layer.
    """
    layers: list[dict[str, Any]] = []

    user_path = Path.home() / ".brain" / "settings.json"
    layers.append(_read_json(user_path))

    repo_path = repo / ".brain" / "settings.json"
    layers.append(_read_json(repo_path))

    enterprise_url = os.environ.get("BRAIN_ENTERPRISE_CONFIG_URL", "").strip()
    if enterprise_url:
        layers.append(_fetch_enterprise(enterprise_url))

    merged: dict[str, Any] = {}
    for layer in layers:
        merged = _deep_merge(merged, layer)
    return merged


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            log.warning("workspace.settings.not_object", path=str(path))
            return {}
        return data
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("workspace.settings.read_error", path=str(path), error=str(exc))
        return {}


def _fetch_enterprise(url: str) -> dict[str, Any]:
    """Pull the org-wide JSON. Soft-fails on any error so the CLI keeps working."""
    try:
        import httpx
        resp = httpx.get(url, timeout=5.0)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, dict):
            log.warning("workspace.enterprise.not_object", url=url)
            return {}
        return data
    except Exception as exc:  # noqa: BLE001 — settings must never break the CLI
        log.warning("workspace.enterprise.fetch_error", url=url, error=str(exc))
        return {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursive merge: dicts combine key-by-key, scalars and lists overwrite."""
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _resolve_commit_sha(repo: Path) -> str | None:
    """``git rev-parse HEAD`` for ``repo``; returns None if not a git checkout."""
    try:
        result = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return None
        sha = result.stdout.decode().strip()
        return sha or None
    except (OSError, subprocess.SubprocessError):
        return None


__all__ = ["Workspace"]
