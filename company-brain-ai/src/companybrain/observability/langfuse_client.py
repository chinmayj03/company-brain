"""
LangfuseTracker — sends LLM call telemetry to Langfuse.

Langfuse tracks:
  - Every LLM call: model, tokens, cost, latency, prompt version
  - Trace hierarchies: one Trace per pipeline run, Generations per LLM call
  - Prompt versions: system prompts are versioned so we can compare quality

Falls back silently if Langfuse is unconfigured or unavailable.

Usage::
    tracker = get_tracker()

    # Start a trace for a pipeline run
    trace = tracker.trace(name="pipeline_run", job_id="abc123", workspace="ws1")

    # Record an LLM call
    tracker.generation(
        trace_id=trace.id,
        name="entity_extraction/CompetitivenessController",
        model="claude-haiku-4-5",
        prompt_tokens=1842,
        completion_tokens=214,
        cost_usd=0.000218,
        input_text="...",    # truncated system+user
        output_text="...",   # truncated response
    )

    # Finalize trace
    tracker.finalize(trace_id=trace.id, status="success")
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from typing import Optional, Any
import structlog

log = structlog.get_logger(__name__)

_tracker_instance: Optional["LangfuseTracker"] = None


@dataclass
class TraceHandle:
    id: str
    name: str
    started_at: float = field(default_factory=time.time)


class LangfuseTracker:
    """
    Thin wrapper around the Langfuse Python SDK.
    All methods are no-ops if Langfuse is not configured.
    """

    def __init__(self, public_key: str = "", secret_key: str = "", host: str = ""):
        self._enabled = bool(public_key and secret_key)
        self._client: Any = None

        if self._enabled:
            try:
                from langfuse import Langfuse  # type: ignore
                self._client = Langfuse(
                    public_key=public_key,
                    secret_key=secret_key,
                    host=host or "https://cloud.langfuse.com",
                )
                log.info("Langfuse connected", host=host)
            except ImportError:
                log.warning("langfuse package not installed — observability disabled")
                self._enabled = False
            except Exception as e:
                log.warning("Langfuse init failed", error=str(e))
                self._enabled = False

    @property
    def enabled(self) -> bool:
        return self._enabled

    def trace(self, name: str, job_id: str = "", workspace: str = "", metadata: dict | None = None) -> TraceHandle:
        """Start a new trace for a pipeline run. Returns a handle for subsequent calls."""
        handle = TraceHandle(id=job_id or f"cb-{int(time.time()*1000)}", name=name)
        if not self._enabled or self._client is None:
            return handle
        try:
            self._client.trace(
                id=handle.id,
                name=name,
                metadata={"workspace": workspace, **(metadata or {})},
            )
        except Exception as e:
            log.debug("Langfuse trace failed", error=str(e))
        return handle

    def generation(
        self,
        trace_id: str,
        name: str,
        model: str,
        prompt_tokens: int,
        completion_tokens: int,
        cost_usd: float,
        input_text: str = "",
        output_text: str = "",
        metadata: dict | None = None,
    ) -> None:
        """Record a single LLM call as a Langfuse Generation."""
        if not self._enabled or self._client is None:
            return
        try:
            self._client.generation(
                trace_id=trace_id,
                name=name,
                model=model,
                usage={"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens},
                input=input_text[:2000] if input_text else "",
                output=output_text[:2000] if output_text else "",
                metadata={"cost_usd": cost_usd, **(metadata or {})},
            )
        except Exception as e:
            log.debug("Langfuse generation failed", error=str(e))

    def finalize(self, trace_id: str, status: str = "success", error: str = "") -> None:
        """Mark a trace as complete."""
        if not self._enabled or self._client is None:
            return
        try:
            self._client.trace(id=trace_id, metadata={"status": status, "error": error})
            self._client.flush()
        except Exception as e:
            log.debug("Langfuse finalize failed", error=str(e))


def get_tracker() -> LangfuseTracker:
    """Return the singleton LangfuseTracker instance."""
    global _tracker_instance
    if _tracker_instance is None:
        from companybrain.config import settings
        _tracker_instance = LangfuseTracker(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    return _tracker_instance
