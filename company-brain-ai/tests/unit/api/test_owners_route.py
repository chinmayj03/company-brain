"""Unit tests for GET /entities/{urn}/owners — ADR-0073 R3."""
from __future__ import annotations

from unittest.mock import patch, AsyncMock

import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI

from companybrain.api.routes.owners import router

UNKNOWN_URN = "urn%3Acb%3Afn%3ADoesNotExist"


@pytest.fixture()
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


def _mock_no_db():
    cm = AsyncMock()
    cm.return_value.__aenter__ = AsyncMock(side_effect=RuntimeError("no db"))
    cm.return_value.__aexit__ = AsyncMock(return_value=False)
    return patch("companybrain.api.routes.owners.get_session", return_value=cm.return_value)


def test_owners_unknown_urn_returns_empty(client):
    with _mock_no_db():
        resp = client.get(f"/{UNKNOWN_URN}/owners")
    assert resp.status_code == 200
    body = resp.json()
    assert body["owners"] == []
    assert body["bus_factor"] == 0


def test_owners_returns_required_shape(client):
    with _mock_no_db():
        resp = client.get(f"/{UNKNOWN_URN}/owners")
    assert resp.status_code == 200
    body = resp.json()
    assert "urn" in body
    assert "owners" in body
    assert "bus_factor" in body
    assert isinstance(body["owners"], list)
    assert isinstance(body["bus_factor"], int)


def test_owners_never_500(client):
    """Even with a completely broken DB + git, the route must not 500."""
    with _mock_no_db():
        with patch("subprocess.check_output", side_effect=RuntimeError("boom")):
            resp = client.get(f"/{UNKNOWN_URN}/owners")
    assert resp.status_code == 200
