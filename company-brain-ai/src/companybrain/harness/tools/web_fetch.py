"""web_fetch — agent-driven HTTP GET for documentation enrichment (ADR-0052 P5).

Sub-agents (typically the BusinessContext synthesiser) use this to fetch a
single web page — usually framework documentation referenced from an ADR
('what does ``@RestController`` actually do?' answered by Spring's docs).

Safety net:
  * Capability gate ``NETWORK`` — defaults to ASK; non-interactive runs
    must opt in via ``--allow-net`` / ``--yes`` / ``BRAIN_GRANTS=network:auto``.
  * Hard URL allow-list: only ``http://`` and ``https://`` schemes; no file://,
    no chrome-extension://. We refuse anything else without a network call.
  * Output cap (10 KB) — trimmed at the byte level after content-type sniff.
  * Timeout: 10 seconds per request.

Returns a dict so the agent can branch on ``status_code`` and ``content_type``.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

import structlog

from companybrain.harness.permissions import Capability
from companybrain.harness.tools import register_tool
from companybrain.llm.base import ToolParameter

log = structlog.get_logger(__name__)


_TIMEOUT_S      = 10.0
_MAX_BODY_BYTES = 10_240


@register_tool(
    name="web_fetch",
    description=(
        "Fetch a single URL via HTTPS. Capability NETWORK (denied / ask by "
        "default). Body is truncated to 10 KB. Use to enrich BusinessContext "
        "with framework documentation when the snippet alone is ambiguous."
    ),
    parameters=[
        ToolParameter("url", "string", "Absolute http(s) URL to fetch."),
    ],
    requires=(Capability.NETWORK,),
)
async def handler(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    url = (args.get("url") or "").strip()
    if not url:
        return {"ok": False, "error": "web_fetch: empty 'url'"}

    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return {
            "ok":    False,
            "error": f"web_fetch refuses scheme {parsed.scheme!r} — only http/https allowed",
        }

    try:
        import httpx
    except ImportError:
        return {"ok": False, "error": "web_fetch: httpx is not installed"}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=True) as client:
            resp = await client.get(url)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    body = resp.text
    truncated = False
    if len(body.encode("utf-8", errors="replace")) > _MAX_BODY_BYTES:
        body = body.encode("utf-8", errors="replace")[:_MAX_BODY_BYTES].decode(
            "utf-8", errors="replace"
        )
        truncated = True

    return {
        "ok":            resp.is_success,
        "url":           str(resp.url),
        "status_code":   resp.status_code,
        "content_type":  resp.headers.get("content-type", ""),
        "body":          body,
        "truncated":     truncated,
    }
