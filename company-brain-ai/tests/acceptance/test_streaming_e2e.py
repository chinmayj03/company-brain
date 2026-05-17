"""
A1.5 acceptance tests — SSE streaming endpoint.

Tests
-----
1. POST /query/stream/v2 returns ``text/event-stream`` content-type.
2. First SSE event has ``type == "token"``.
3. Last SSE event has ``type == "done"`` with required fields.
4. Non-streaming POST /query still returns JSON 200 (regression guard).
5. When ``STREAMING_ENABLED=false``, /query/stream/v2 returns JSON (flag bypass).

The LLM provider is mocked so no real API calls are made and the suite
runs offline.  TTFT is timing-sensitive and can only be measured against
a live LLM — the relevant test is marked with ``pytest.mark.live`` so
it is skipped in CI.
"""
from __future__ import annotations

import json
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

WORKSPACE_ID = "00000000-0000-0000-0000-000000000001"

MINIMAL_REQUEST = {
    "question": "What does PaymentService do?",
    "workspace_id": WORKSPACE_ID,
}


def _make_app():
    """Build a minimal FastAPI app with just the query router mounted."""
    from fastapi import FastAPI
    from companybrain.api.routes.query import router

    app = FastAPI()
    app.include_router(router, prefix="/query")
    return app


def _mock_provider(answer: str = "PaymentService handles payments."):
    """Return a mock LLMProvider whose chat() returns ``answer``."""
    from companybrain.llm.base import ChatResponse

    provider = MagicMock()
    provider.provider_name = "mock"
    provider.model_for_role = MagicMock(return_value="mock-model")
    provider.chat = AsyncMock(
        return_value=ChatResponse(
            content=answer,
            model="mock-model",
            provider="mock",
            input_tokens=10,
            output_tokens=20,
        )
    )
    # No native streaming API on the mock provider — emitter will use
    # the chat() fallback path.
    if hasattr(provider, "_client"):
        del provider._client
    return provider


def _parse_sse_events(raw: str) -> list[dict]:
    """Parse SSE text (lines starting with ``data:``) into a list of dicts."""
    events = []
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            payload = line[len("data:"):].strip()
            if payload and payload != "[DONE]":
                try:
                    events.append(json.loads(payload))
                except json.JSONDecodeError:
                    pass
    return events


# ---------------------------------------------------------------------------
# Test 1 — /query/stream/v2 returns text/event-stream
# ---------------------------------------------------------------------------

def test_stream_v2_content_type():
    """POST /query/stream/v2 must return Content-Type: text/event-stream."""
    provider = _mock_provider()

    async def _fake_sse(*_args, **_kwargs) -> AsyncIterator[str]:
        yield 'data: {"type": "token", "text": "hello"}\n\n'
        yield (
            'data: {"type": "done", "citations": [], "confidence": '
            '{"level": "low", "rationale": "test"}, '
            '"matched_shape_id": null, "from_cache": false}\n\n'
        )

    app = _make_app()
    with (
        patch("companybrain.api.routes.query.get_provider", return_value=provider),
        patch(
            "companybrain.api.routes.query._smart_zone_assemble",
            new=AsyncMock(return_value=(None, {})),
        ),
        patch(
            "companybrain.api.routes.query._hybrid_retrieve_sync",
            return_value=None,
            create=True,
        ),
        patch(
            "companybrain.api.routes.query.stream_query_response",
            side_effect=_fake_sse,
            create=True,
        ),
    ):
        client = TestClient(app, raise_server_exceptions=True)
        resp = client.post("/query/stream/v2", json=MINIMAL_REQUEST)

    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Test 2 — first event has type == "token"
# ---------------------------------------------------------------------------

def test_stream_v2_first_event_is_token():
    """The first SSE event from /query/stream/v2 must have type == 'token'."""
    provider = _mock_provider()

    TOKEN_TEXT = "PaymentService handles payments."

    async def _fake_sse(*_args, **_kwargs) -> AsyncIterator[str]:
        yield f'data: {{"type": "token", "text": "{TOKEN_TEXT}"}}\n\n'
        yield (
            'data: {"type": "done", "citations": [], "confidence": '
            '{"level": "low", "rationale": "test"}, '
            '"matched_shape_id": null, "from_cache": false}\n\n'
        )

    app = _make_app()
    with (
        patch("companybrain.api.routes.query.get_provider", return_value=provider),
        patch(
            "companybrain.api.routes.query._smart_zone_assemble",
            new=AsyncMock(return_value=(None, {})),
        ),
        patch(
            "companybrain.api.routes.query._hybrid_retrieve_sync",
            return_value=None,
            create=True,
        ),
        patch(
            "companybrain.api.routes.query.stream_query_response",
            side_effect=_fake_sse,
            create=True,
        ),
    ):
        client = TestClient(app)
        resp = client.post("/query/stream/v2", json=MINIMAL_REQUEST)

    events = _parse_sse_events(resp.text)
    assert events, "No SSE events parsed"
    assert events[0]["type"] == "token", f"Expected 'token', got: {events[0]}"
    assert TOKEN_TEXT in events[0]["text"]


# ---------------------------------------------------------------------------
# Test 3 — last event has type == "done" with required fields
# ---------------------------------------------------------------------------

def test_stream_v2_last_event_is_done():
    """The last SSE event must be type='done' with citations, confidence, from_cache."""
    provider = _mock_provider()

    CITATIONS = [{"urn": "urn:cb:service:PaymentService", "name": "PaymentService"}]

    async def _fake_sse(*_args, **_kwargs) -> AsyncIterator[str]:
        yield 'data: {"type": "token", "text": "answer text"}\n\n'
        yield (
            f'data: {{"type": "done", "citations": {json.dumps(CITATIONS)}, '
            f'"confidence": {{"level": "medium", "rationale": "ok"}}, '
            f'"matched_shape_id": null, "from_cache": false}}\n\n'
        )

    app = _make_app()
    with (
        patch("companybrain.api.routes.query.get_provider", return_value=provider),
        patch(
            "companybrain.api.routes.query._smart_zone_assemble",
            new=AsyncMock(return_value=(None, {})),
        ),
        patch(
            "companybrain.api.routes.query._hybrid_retrieve_sync",
            return_value=None,
            create=True,
        ),
        patch(
            "companybrain.api.routes.query.stream_query_response",
            side_effect=_fake_sse,
            create=True,
        ),
    ):
        client = TestClient(app)
        resp = client.post("/query/stream/v2", json=MINIMAL_REQUEST)

    events = _parse_sse_events(resp.text)
    assert events, "No SSE events parsed"
    done_events = [e for e in events if e.get("type") == "done"]
    assert done_events, f"No 'done' event found. Events: {events}"
    done = done_events[-1]
    assert "citations" in done, "done event missing 'citations'"
    assert "confidence" in done, "done event missing 'confidence'"
    assert "from_cache" in done, "done event missing 'from_cache'"
    assert done["from_cache"] is False


# ---------------------------------------------------------------------------
# Test 4 — regression: POST /query still returns JSON 200
# ---------------------------------------------------------------------------

def test_non_streaming_query_returns_json():
    """POST /query (non-streaming) must return 200 JSON with 'summary' field."""
    from companybrain.llm.base import ChatResponse
    from companybrain.models.query_response import Confidence, QueryResponse

    provider = _mock_provider()

    # The handler will parse the LLM response as JSON — supply a valid one.
    qr = QueryResponse(
        summary="PaymentService is the payment processing service.",
        confidence=Confidence(level="high", rationale="direct match"),
    )
    try:
        llm_answer = qr.model_dump_json()
    except AttributeError:
        llm_answer = qr.json()

    provider.chat = AsyncMock(
        return_value=ChatResponse(
            content=llm_answer,
            model="mock-model",
            provider="mock",
        )
    )

    app = _make_app()
    with (
        patch("companybrain.api.routes.query.get_provider", return_value=provider),
        patch(
            "companybrain.api.routes.query._smart_zone_assemble",
            new=AsyncMock(return_value=(None, {})),
        ),
        patch(
            "companybrain.api.routes.query._hybrid_retrieve",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "companybrain.api.routes.query._attach_notes",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "companybrain.api.routes.query._persist_conversation",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "companybrain.api.routes.query._attach_adr_0059_derivatives",
            return_value=None,
        ),
        patch(
            "companybrain.api.routes.query._attach_cross_repo_insights",
            return_value=None,
        ),
        patch(
            "companybrain.api.routes.query._maybe_explore",
            new=AsyncMock(side_effect=lambda **kw: kw["response"]),
        ),
    ):
        client = TestClient(app)
        resp = client.post("/query", json=MINIMAL_REQUEST)

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "summary" in body, f"Expected 'summary' in response body, got: {list(body.keys())}"


# ---------------------------------------------------------------------------
# Test 5 — STREAMING_ENABLED=false falls back to JSON
# ---------------------------------------------------------------------------

def test_streaming_disabled_flag_returns_json():
    """When settings.streaming_enabled is False, /query/stream/v2 returns JSON."""
    from companybrain.llm.base import ChatResponse
    from companybrain.models.query_response import Confidence, QueryResponse

    provider = _mock_provider()

    qr = QueryResponse(
        summary="PaymentService is the payment processing service.",
        confidence=Confidence(level="high", rationale="direct match"),
    )
    try:
        llm_answer = qr.model_dump_json()
    except AttributeError:
        llm_answer = qr.json()

    provider.chat = AsyncMock(
        return_value=ChatResponse(
            content=llm_answer,
            model="mock-model",
            provider="mock",
        )
    )

    app = _make_app()
    # Patch settings.streaming_enabled = False
    from companybrain import config as _config_mod
    original = _config_mod.settings.streaming_enabled
    try:
        _config_mod.settings.streaming_enabled = False
        with (
            patch("companybrain.api.routes.query.get_provider", return_value=provider),
            patch(
                "companybrain.api.routes.query._smart_zone_assemble",
                new=AsyncMock(return_value=(None, {})),
            ),
            patch(
                "companybrain.api.routes.query._hybrid_retrieve",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "companybrain.api.routes.query._attach_notes",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "companybrain.api.routes.query._persist_conversation",
                new=AsyncMock(return_value=None),
            ),
            patch(
                "companybrain.api.routes.query._attach_adr_0059_derivatives",
                return_value=None,
            ),
            patch(
                "companybrain.api.routes.query._attach_cross_repo_insights",
                return_value=None,
            ),
            patch(
                "companybrain.api.routes.query._maybe_explore",
                new=AsyncMock(side_effect=lambda **kw: kw["response"]),
            ),
        ):
            client = TestClient(app)
            resp = client.post("/query/stream/v2", json=MINIMAL_REQUEST)
    finally:
        _config_mod.settings.streaming_enabled = original

    assert resp.status_code == 200
    # Should be JSON not SSE
    assert "text/event-stream" not in resp.headers.get("content-type", "")
    body = resp.json()
    assert "summary" in body


# ---------------------------------------------------------------------------
# Live TTFT test (skipped in CI — requires a real LLM + running server)
# ---------------------------------------------------------------------------

@pytest.mark.live
@pytest.mark.skip(reason="Requires live LLM and running server (TTFT timing test)")
async def test_stream_v2_ttft_under_one_second():
    """
    TTFT target: first token arrives < 1s on cold cache.

    This test is timing-sensitive and can only be verified against a live
    LLM backend.  Run manually with:
        pytest -m live tests/acceptance/test_streaming_e2e.py
    """
    import time
    import httpx

    BASE_URL = "http://localhost:8000"
    t0 = time.monotonic()
    first_token_at: float | None = None

    async with httpx.AsyncClient(timeout=30.0) as client:
        async with client.stream(
            "POST",
            f"{BASE_URL}/query/stream/v2",
            json=MINIMAL_REQUEST,
        ) as resp:
            assert resp.status_code == 200
            async for line in resp.aiter_lines():
                if line.startswith("data:"):
                    payload = line[len("data:"):].strip()
                    try:
                        event = json.loads(payload)
                        if event.get("type") == "token":
                            first_token_at = time.monotonic()
                            break
                    except json.JSONDecodeError:
                        pass

    assert first_token_at is not None, "No token event received"
    ttft = first_token_at - t0
    assert ttft < 1.0, (
        f"TTFT {ttft:.2f}s exceeds 1s target. "
        "Consider checking network latency, cold-start overhead, or LLM provider."
    )
