"""ADR-0051 Phase 1 — agentic harness for the extraction pipeline.

Public surface:
    HarnessLoop  — prompt-controlled tool-dispatch loop
    HarnessResult — typed return value of HarnessLoop.run()
    TOOL_REGISTRY — name → Tool table (tools register themselves on import)
"""
from companybrain.harness.loop import HarnessLoop, HarnessResult
from companybrain.harness.tools import TOOL_REGISTRY

__all__ = ["HarnessLoop", "HarnessResult", "TOOL_REGISTRY"]
