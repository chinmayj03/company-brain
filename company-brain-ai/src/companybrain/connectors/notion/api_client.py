"""
B1.4 Notion connector — rate-limit-aware async API client.

Implements:
  - 3 req/s token-bucket throttle (configurable)
  - Automatic retry on HTTP 429 using Retry-After header
  - Cursor-based pagination via `paginate()`
"""
from __future__ import annotations

import asyncio
import time
from typing import AsyncIterator, Any

import httpx

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


class NotionApiClient:
    """Async Notion API client with rate limiting (3 req/s) and retry on 429."""

    def __init__(self, api_token: str, requests_per_second: float = 3.0):
        self._token = api_token
        self._min_interval = 1.0 / requests_per_second
        self._last_request_at: float = 0.0
        self._client = httpx.AsyncClient(
            base_url=NOTION_API_BASE,
            headers={
                "Authorization": f"Bearer {api_token}",
                "Notion-Version": NOTION_VERSION,
                "Content-Type": "application/json",
            },
            timeout=30.0,
        )

    async def get(self, path: str, **params: Any) -> dict:
        """GET request with rate limiting and 429 retry."""
        await self._throttle()
        resp = await self._client.get(path, params=params)
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", "2"))
            await asyncio.sleep(retry_after)
            return await self.get(path, **params)
        resp.raise_for_status()
        return resp.json()

    async def post(self, path: str, body: dict) -> dict:
        """POST request with rate limiting and 429 retry."""
        await self._throttle()
        resp = await self._client.post(path, json=body)
        if resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", "2"))
            await asyncio.sleep(retry_after)
            return await self.post(path, body)
        resp.raise_for_status()
        return resp.json()

    async def paginate(
        self,
        path: str,
        body: dict | None = None,
    ) -> AsyncIterator[dict]:
        """
        Paginate a Notion list endpoint, yielding one result object at a time.

        If `body` is provided the endpoint is called via POST (e.g. /search).
        Otherwise it is called via GET (e.g. /blocks/{id}/children).
        """
        cursor: str | None = None
        while True:
            if body is not None:
                # POST-based pagination (search, database query)
                req: dict = {**body, "page_size": 100}
                if cursor:
                    req["start_cursor"] = cursor
                data = await self.post(path, req)
            else:
                # GET-based pagination (block children, etc.)
                params: dict = {"page_size": 100}
                if cursor:
                    params["start_cursor"] = cursor
                data = await self.get(path, **params)

            for item in data.get("results", []):
                yield item

            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")

    async def _throttle(self) -> None:
        """Enforce minimum interval between requests."""
        now = time.monotonic()
        wait = self._min_interval - (now - self._last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request_at = time.monotonic()

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> "NotionApiClient":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()
