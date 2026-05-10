"""Python SDK for company-brain (ADR-0052 P5).

Programmatic access alongside the CLI:

    from companybrain.sdk import CompanyBrain

    brain = CompanyBrain(repo="/path/to/repo")
    result = await brain.extract("/v1/foo", method="POST")
    print(result.entity_count, result.cost_usd)

The SDK is a thin wrapper around the same harness primitives the CLI uses;
it does not duplicate orchestration logic. CLI and SDK share the same
:class:`Workspace`, :class:`HarnessLoop`, and tool registry.
"""
from companybrain.sdk.client import CompanyBrain
from companybrain.sdk.models import (
    DiffResult,
    QueryResponse,
    RunResult,
)

__all__ = ["CompanyBrain", "DiffResult", "QueryResponse", "RunResult"]
