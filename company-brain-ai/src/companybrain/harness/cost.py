"""Per-tool-call cost tracker (ADR-0051 P4).

Each tool dispatch may cost LLM tokens (sub-agents) or be free (pure code
tools). The :class:`CostTracker` aggregates by tool name so a job summary
can show which tools dominated cost — the input the team needs to know
where to cache, batch, or downgrade the model.

The tracker is intentionally a small dataclass with thread-unsafe mutation:
it lives inside one HarnessLoop, which runs on a single asyncio event loop.
Sub-agents flush their own cost back to the parent tracker via :meth:`add`.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CostTracker:
    """Aggregate per-tool token + USD cost across a run.

    `by_tool` is keyed on the tool's registry name (e.g. ``"spawn_extractor"``,
    ``"extract_methods_from_class"``). Cost is computed by the caller (using
    ``compute_cost_usd`` from :mod:`companybrain.llm.base`) — the tracker
    only sums.
    """

    by_tool: dict[str, dict[str, Any]] = field(
        default_factory=lambda: defaultdict(lambda: {
            "calls":         0,
            "input_tokens":  0,
            "output_tokens": 0,
            "cost_usd":      0.0,
        })
    )
    total_cost_usd: float = 0.0
    total_calls:    int   = 0

    def add(
        self,
        tool: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Roll one tool-call's cost into the running totals."""
        slot = self.by_tool[tool]
        slot["calls"]         += 1
        slot["input_tokens"]  += int(input_tokens or 0)
        slot["output_tokens"] += int(output_tokens or 0)
        slot["cost_usd"]      += float(cost_usd or 0.0)
        self.total_cost_usd   += float(cost_usd or 0.0)
        self.total_calls      += 1

    def summary(self) -> dict[str, Any]:
        """Compact view for telemetry: total + per-tool, sorted by cost desc."""
        # Sort by cost desc so the dominant tool is at the top of the dict.
        # We still emit it as a dict — JSON preserves insertion order in
        # every modern decoder, so the sort survives serialisation.
        ordered = sorted(
            self.by_tool.items(),
            key=lambda kv: -kv[1]["cost_usd"],
        )
        return {
            "total_cost_usd": round(self.total_cost_usd, 4),
            "total_calls":    self.total_calls,
            "by_tool": {
                tool: {
                    "calls":         data["calls"],
                    "input_tokens":  data["input_tokens"],
                    "output_tokens": data["output_tokens"],
                    "cost_usd":      round(data["cost_usd"], 4),
                }
                for tool, data in ordered
            },
        }


__all__ = ["CostTracker"]
