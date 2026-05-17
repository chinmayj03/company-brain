"""Unit tests for GET /workspaces/{id}/repos and /branches — ADR-0073 R1+R2."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from unittest.mock import patch, AsyncMock

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from companybrain.api.routes.repos import router

WS_ID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture()
def tmp_git_repo(tmp_path):
    """Initialise a minimal git repo and return its path."""
    subprocess.check_call(["git", "init", str(tmp_path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    subprocess.check_call(["git", "-C", str(tmp_path), "commit", "--allow-empty", "-m", "init"],
                          env={**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@t.com",
                               "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@t.com"},
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return str(tmp_path)


# ── /repos ────────────────────────────────────────────────────────────────────

def _mock_no_db():
    """Context manager: make DB lookup raise so we fall through to env fallback."""
    cm = AsyncMock()
    cm.return_value.__aenter__ = AsyncMock(side_effect=RuntimeError("no db"))
    cm.return_value.__aexit__ = AsyncMock(return_value=False)
    return patch("companybrain.api.routes.repos.get_session", return_value=cm.return_value)


def test_repos_returns_list_from_env(client, tmp_git_repo):
    with _mock_no_db(), patch.dict(os.environ, {"CB_REPO_PATH": tmp_git_repo}):
        resp = client.get(f"/{WS_ID}/repos")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    repo = data[0]
    assert repo["repo_path"] == tmp_git_repo
    assert repo["sync_status"] == "ok"
    assert "current_branch" in repo


def test_repos_returns_empty_when_no_source(client, tmp_path):
    # Use a manifest path that doesn't exist so the manifest fallback also misses
    with _mock_no_db(), \
         patch.dict(os.environ, {}, clear=True), \
         patch("companybrain.api.routes.repos.Path.home", return_value=tmp_path):
        os.environ.pop("CB_REPO_PATH", None)
        resp = client.get(f"/{WS_ID}/repos")
    assert resp.status_code == 200
    assert resp.json() == []


def test_repos_marks_missing_path_as_error(client, tmp_path):
    missing = str(tmp_path / "does_not_exist")
    with _mock_no_db(), patch.dict(os.environ, {"CB_REPO_PATH": missing}):
        resp = client.get(f"/{WS_ID}/repos")
    assert resp.status_code == 200
    data = resp.json()
    assert data[0]["sync_status"] == "error"


# ── /branches ─────────────────────────────────────────────────────────────────

def test_branches_returns_list(client, tmp_git_repo):
    with _mock_no_db(), patch.dict(os.environ, {"CB_REPO_PATH": tmp_git_repo}):
        resp = client.get(f"/{WS_ID}/repos/env-repo/branches")
    assert resp.status_code == 200
    body = resp.json()
    assert "current" in body
    assert "branches" in body
    assert isinstance(body["branches"], list)
    assert len(body["branches"]) >= 1


def test_branches_falls_back_gracefully_for_unknown_repo(client):
    with _mock_no_db():
        resp = client.get(f"/{WS_ID}/repos/unknown-id/branches")
    assert resp.status_code == 200
    body = resp.json()
    assert body["current"] == "main"
    assert "main" in body["branches"]
