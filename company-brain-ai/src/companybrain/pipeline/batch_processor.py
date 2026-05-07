"""
BatchProcessor — Anthropic Message Batches API for deferred enrichment.

The Batch API processes requests within 24 hours at 50% cost discount.
Use for non-blocking enrichment: context synthesis, business-bridge inferences,
narrative summaries that don't need to be ready before the graph write.

Usage::
    processor = BatchProcessor()

    # Queue items for batch enrichment
    batch_id = await processor.submit_batch(
        requests=[
            BatchRequest(custom_id="entity:abc123", system=SYSTEM_PROMPT, user="..."),
            BatchRequest(custom_id="entity:def456", system=SYSTEM_PROMPT, user="..."),
        ],
        model="claude-haiku-4-5-20251001",
    )

    # Poll for results (separate job — can be hours later)
    results = await processor.poll_batch(batch_id)
    for r in results:
        print(r.custom_id, r.content)

The primary use case is context_synthesizer.py Stage 3 — if the pipeline
is running in "deferred" mode, context synthesis is queued as a batch instead
of blocking the pipeline.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Optional
import structlog

log = structlog.get_logger(__name__)


@dataclass
class BatchRequest:
    custom_id: str       # identifier to match results
    system: str          # system prompt
    user: str            # user message
    max_tokens: int = 1024


@dataclass
class BatchResult:
    custom_id: str
    content: str
    error: Optional[str] = None
    input_tokens: int = 0
    output_tokens: int = 0


class BatchProcessor:
    """
    Wraps the Anthropic Message Batches API.
    Falls back to sequential processing if Batch API is unavailable.
    """

    def __init__(self, api_key: str = ""):
        from companybrain.config import settings
        self._api_key = api_key or getattr(settings, "anthropic_api_key", "")
        self._client = None
        self._log = structlog.get_logger(__name__)

    def _get_client(self):
        if self._client is None:
            try:
                from anthropic import AsyncAnthropic
                self._client = AsyncAnthropic(api_key=self._api_key)
            except Exception as e:
                log.warning("Anthropic client init failed", error=str(e))
        return self._client

    async def submit_batch(
        self,
        requests: list[BatchRequest],
        model: str = "claude-haiku-4-5-20251001",
    ) -> str:
        """
        Submit a batch of requests. Returns the batch ID.
        Raises on failure (caller should fall back to sequential processing).
        """
        client = self._get_client()
        if client is None:
            raise RuntimeError("Anthropic client unavailable")

        batch_requests = [
            {
                "custom_id": req.custom_id,
                "params": {
                    "model": model,
                    "max_tokens": req.max_tokens,
                    "system": req.system,
                    "messages": [{"role": "user", "content": req.user}],
                },
            }
            for req in requests
        ]

        batch = await client.beta.messages.batches.create(requests=batch_requests)
        log.info("Batch submitted", batch_id=batch.id, size=len(requests), model=model)
        return batch.id

    async def poll_batch(
        self,
        batch_id: str,
        max_wait_seconds: int = 86_400,  # 24h
        poll_interval: int = 60,
    ) -> list[BatchResult]:
        """
        Poll a batch until complete. Returns results.
        Times out after max_wait_seconds.
        """
        client = self._get_client()
        if client is None:
            return []

        deadline = time.time() + max_wait_seconds
        while time.time() < deadline:
            batch = await client.beta.messages.batches.retrieve(batch_id)
            if batch.processing_status == "ended":
                break
            log.debug("Batch pending", batch_id=batch_id, status=batch.processing_status)
            await asyncio.sleep(poll_interval)

        results = []
        async for result in await client.beta.messages.batches.results(batch_id):
            if result.result.type == "succeeded":
                content = result.result.message.content[0].text if result.result.message.content else ""
                results.append(BatchResult(
                    custom_id=result.custom_id,
                    content=content,
                    input_tokens=result.result.message.usage.input_tokens,
                    output_tokens=result.result.message.usage.output_tokens,
                ))
            else:
                results.append(BatchResult(
                    custom_id=result.custom_id,
                    content="",
                    error=str(result.result),
                ))

        log.info("Batch complete", batch_id=batch_id, results=len(results))
        return results

    async def process_with_fallback(
        self,
        requests: list[BatchRequest],
        model: str = "claude-haiku-4-5-20251001",
        use_batch: bool = False,
    ) -> list[BatchResult]:
        """
        Process requests via Batch API (if use_batch=True and >10 requests)
        or sequentially. Always returns results.
        """
        if use_batch and len(requests) >= 10:
            try:
                batch_id = await self.submit_batch(requests, model=model)
                return await self.poll_batch(batch_id)
            except Exception as e:
                log.warning("Batch API failed, falling back to sequential", error=str(e))

        # Sequential fallback
        results = []
        client = self._get_client()
        if client is None:
            return []
        for req in requests:
            try:
                response = await client.messages.create(
                    model=model,
                    max_tokens=req.max_tokens,
                    system=req.system,
                    messages=[{"role": "user", "content": req.user}],
                )
                content = response.content[0].text if response.content else ""
                results.append(BatchResult(
                    custom_id=req.custom_id,
                    content=content,
                    input_tokens=response.usage.input_tokens,
                    output_tokens=response.usage.output_tokens,
                ))
            except Exception as e:
                results.append(BatchResult(custom_id=req.custom_id, content="", error=str(e)))
        return results
