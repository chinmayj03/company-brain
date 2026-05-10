"""web_search — agent-driven web search via DuckDuckGo HTML (ADR-0052 P5).

A no-key fallback so the harness has *some* web-search capability out of the
box. Production deployments should swap the backend by setting
``BRAIN_SEARCH_BACKEND_URL`` to a Google CSE / Bing endpoint and
``BRAIN_SEARCH_API_KEY`` for auth — the handler will use those when set.

The tool returns a list of ``{title, url, snippet}`` dicts capped at 10 hits.
Capability is ``NETWORK`` — same gate as :mod:`web_fetch`.
"""
from __future__ import annotations

import os
import re
from typing import Any

import structlog

from companybrain.harness.permissions import Capability
from companybrain.harness.tools import register_tool
from companybrain.llm.base import ToolParameter

log = structlog.get_logger(__name__)


_TIMEOUT_S = 10.0
_MAX_HITS  = 10
_DDG_URL   = "https://duckduckgo.com/html/"


@register_tool(
    name="web_search",
    description=(
        "Search the web with a free DuckDuckGo backend (or a configured search "
        "API). Returns up to 10 {title,url,snippet} hits. Capability NETWORK "
        "(denied / ask by default)."
    ),
    parameters=[
        ToolParameter("query", "string", "Search query, e.g. 'spring boot @RestController docs'."),
        ToolParameter("max_results", "integer",
                      "Cap on returned hits (default 5, max 10).", required=False),
    ],
    requires=(Capability.NETWORK,),
)
async def handler(args: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    query = (args.get("query") or "").strip()
    if not query:
        return {"ok": False, "error": "web_search: empty 'query'"}

    max_results = max(1, min(int(args.get("max_results") or 5), _MAX_HITS))

    backend_url = os.environ.get("BRAIN_SEARCH_BACKEND_URL")
    if backend_url:
        return await _search_via_backend(backend_url, query, max_results)
    return await _search_via_duckduckgo(query, max_results)


# ── backends ───────────────────────────────────────────────────────────────


async def _search_via_backend(url: str, query: str, max_results: int) -> dict[str, Any]:
    """Call a configured JSON search backend (Google CSE / Bing-shaped).

    Expected response shape: ``{"items": [{"title", "link", "snippet"}, ...]}``.
    """
    try:
        import httpx
    except ImportError:
        return {"ok": False, "error": "web_search: httpx is not installed"}

    headers = {}
    api_key = os.environ.get("BRAIN_SEARCH_API_KEY", "").strip()
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S) as client:
            resp = await client.get(url, params={"q": query, "num": max_results}, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return {"ok": False, "error": "search backend returned no 'items' array"}
    hits = [
        {
            "title":   str(it.get("title", "")),
            "url":     str(it.get("link") or it.get("url", "")),
            "snippet": str(it.get("snippet", "")),
        }
        for it in items[:max_results]
    ]
    return {"ok": True, "query": query, "results": hits}


async def _search_via_duckduckgo(query: str, max_results: int) -> dict[str, Any]:
    """Lightweight DuckDuckGo HTML scrape — works without an API key."""
    try:
        import httpx
    except ImportError:
        return {"ok": False, "error": "web_search: httpx is not installed"}

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_S, follow_redirects=True) as client:
            resp = await client.post(
                _DDG_URL,
                data={"q": query},
                headers={"User-Agent": "company-brain-search/1.0"},
            )
            resp.raise_for_status()
            html = resp.text
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

    return {"ok": True, "query": query, "results": _parse_ddg_html(html, max_results)}


# Very narrow regex — just enough to handle DDG's stable result block. We
# deliberately do not depend on BeautifulSoup so the harness keeps a small
# dep surface. If DDG changes its markup the operator should switch to a
# proper search backend via BRAIN_SEARCH_BACKEND_URL.
_RESULT_RE = re.compile(
    r'<a[^>]+class="result__a"[^>]+href="(?P<url>[^"]+)"[^>]*>(?P<title>.*?)</a>'
    r'.*?<a[^>]+class="result__snippet"[^>]*>(?P<snippet>.*?)</a>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _parse_ddg_html(html: str, max_results: int) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for m in _RESULT_RE.finditer(html):
        out.append({
            "title":   _strip_tags(m.group("title")),
            "url":     _strip_tags(m.group("url")),
            "snippet": _strip_tags(m.group("snippet")),
        })
        if len(out) >= max_results:
            break
    return out


def _strip_tags(s: str) -> str:
    return _TAG_RE.sub("", s).strip()
