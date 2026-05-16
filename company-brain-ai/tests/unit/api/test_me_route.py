"""Unit tests for GET /me — ADR-0073 R4."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from companybrain.api.routes.me import router


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def test_me_returns_required_fields(client):
    resp = client.get("/me")
    assert resp.status_code == 200
    body = resp.json()
    assert "display_name" in body
    assert "workspace_id" in body
    assert "email" in body
    assert "id" in body
    assert "workspace_name" in body


def test_me_uses_env_vars(client):
    with patch.dict(os.environ, {
        "CB_USER_NAME": "Test User",
        "CB_USER_EMAIL": "test@example.com",
        "CB_WORKSPACE_ID": "aaaaaaaa-0000-0000-0000-000000000001",
        "CB_WORKSPACE_NAME": "my-project",
    }):
        resp = client.get("/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "Test User"
    assert body["email"] == "test@example.com"
    assert body["workspace_id"] == "aaaaaaaa-0000-0000-0000-000000000001"
    assert body["workspace_name"] == "my-project"


def test_me_falls_back_to_defaults_when_no_env(client, monkeypatch):
    # Remove all CB_ vars
    monkeypatch.delenv("CB_USER_NAME", raising=False)
    monkeypatch.delenv("CB_USER_EMAIL", raising=False)
    monkeypatch.delenv("CB_WORKSPACE_ID", raising=False)
    monkeypatch.delenv("CB_WORKSPACE_NAME", raising=False)

    with patch("companybrain.api.routes.me._git_config", return_value=None):
        resp = client.get("/me")
    assert resp.status_code == 200
    body = resp.json()
    assert body["display_name"] == "You"
    assert body["workspace_id"] == "00000000-0000-0000-0000-000000000001"
