"""SDK return types — typed mirrors of the harness telemetry payloads."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunResult:
    """Result of one extraction (or extraction-like) harness run.

    Mirrors the headline fields of :class:`HarnessResult.telemetry` so SDK
    callers don't have to learn the harness's internal shape.
    """

    success: bool
    final_text: str = ""
    iterations: int = 0
    tool_calls_total: int = 0
    tool_calls_ok: int = 0
    cost_usd: float = 0.0
    skill_loaded: str | None = None
    brain_md_loaded: bool = False
    telemetry: dict[str, Any] = field(default_factory=dict)
    command_routed: str | None = None     # set when the run came from a slash command
    entity_count: int = 0                  # populated by extract() from the brain store

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.success


@dataclass
class QueryResponse:
    """Result of :meth:`CompanyBrain.query`."""

    answer: str
    citations: list[str] = field(default_factory=list)
    cost_usd: float = 0.0
    telemetry: dict[str, Any] = field(default_factory=dict)


@dataclass
class DiffResult:
    """Result of :meth:`CompanyBrain.diff` — list of files changed between refs."""

    branch_a: str
    branch_b: str
    files: list[str] = field(default_factory=list)

    @property
    def files_count(self) -> int:
        return len(self.files)


__all__ = ["RunResult", "QueryResponse", "DiffResult"]
