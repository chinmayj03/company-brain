"""SSE endpoint for live job progress (ADR-0051 P4).

Subscribes to the :class:`TodoList` of a running session and yields each
``add`` / ``update`` event as a Server-Sent Event line. Replaces the
existing 2-second polling loop on ``/pipeline/jobs/{id}``.

Format
------

Each event is a JSON object:

    data: {"action":"add","item":{"id":"...","status":"pending",...}}\n\n

A heartbeat is emitted every ``HEARTBEAT_S`` seconds so proxies do not
close idle connections:

    data: {"action":"heartbeat","ts":"2026-05-10T18:00:00+00:00"}\n\n

The stream terminates with ``data: [DONE]\\n\\n`` when the session reaches a
terminal status (``completed`` / ``failed`` / ``timeout``) or the client
disconnects.

Replay on connect
-----------------

The first event after the connect handshake is ``{"action":"snapshot",
"items": [...]}``. That delivers the full TodoList tree as it stood when
the client connected, so a UI that joins mid-run sees the existing items
without waiting for the next mutation.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from companybrain.harness import session as session_mod

router = APIRouter()
log = structlog.get_logger(__name__)


# How often to emit a heartbeat when no real events are flowing. 15s sits
# comfortably below typical proxy idle timeouts (nginx 60s, ALB 60s).
HEARTBEAT_S: float = 15.0

# Terminal statuses that close the stream.
_TERMINAL = {"completed", "failed", "timeout"}


def _format_event(payload: dict) -> str:
    """One SSE event line — a single ``data:`` field with JSON body."""
    return f"data: {json.dumps(payload, default=str)}\n\n"


@router.get("/pipeline/jobs/{job_id}/stream")
async def stream_progress(request: Request, job_id: str) -> StreamingResponse:
    """SSE feed of TodoList add/update events for ``job_id``.

    Returns 404 if the session is not registered (e.g. unknown id, restart).
    """
    sess = session_mod.get_session_or_none(job_id)
    if sess is None:
        raise HTTPException(status_code=404, detail=f"Unknown session: {job_id}")

    return StreamingResponse(
        _generate(request, sess),
        media_type="text/event-stream",
        headers={
            "Cache-Control":      "no-cache",
            "Connection":         "keep-alive",
            "X-Accel-Buffering":  "no",     # nginx: do not buffer SSE
        },
    )


async def _generate(request: Request, sess) -> AsyncGenerator[str, None]:  # noqa: ANN001
    """Yield SSE-formatted strings for one client; cleans up on disconnect."""
    queue: asyncio.Queue[dict] = asyncio.Queue()

    def _enqueue(action: str, item: dict) -> None:
        # Listeners are sync — we drop into the queue without awaiting.
        # asyncio.Queue.put_nowait is safe from sync code as long as we're
        # on the same event loop, which we are: the listener fires inside
        # the harness's loop (the only loop in the process by design).
        try:
            queue.put_nowait({"action": action, "item": item})
        except asyncio.QueueFull:  # pragma: no cover — unbounded queue
            log.warning("sse.queue_full", session=sess.id)

    unsubscribe = sess.todo.subscribe(_enqueue)

    try:
        # 1. Initial snapshot — clients that join mid-run get the existing tree.
        yield _format_event({"action": "snapshot", "items": sess.todo.snapshot()})

        # 2. Live event loop with periodic heartbeat.
        while True:
            if await request.is_disconnected():
                log.debug("sse.disconnect", session=sess.id)
                break
            try:
                ev = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
            except TimeoutError:
                yield _format_event({
                    "action": "heartbeat",
                    "ts": datetime.now(UTC).isoformat(timespec="seconds"),
                })
                # If the session is terminal but we never saw a final event,
                # close the stream so clients can stop reading.
                if sess.status in _TERMINAL:
                    break
                continue
            yield _format_event(ev)
            if sess.status in _TERMINAL and queue.empty():
                break

        yield "data: [DONE]\n\n"
    finally:
        unsubscribe()
