"""CompanyBrain — Python SDK facade for the harness (ADR-0052 P5).

Lifts the most common shell-out workflows into Python so embedded callers
(IDE plugins, notebooks, integration tests) don't have to spawn the CLI.

The SDK is a thin convenience layer:

  * :meth:`extract`  drives :class:`HarnessLoop` with the ``/extract`` slash
    command pre-rendered into the user message.
  * :meth:`query`    drives the same loop with ``/query``.
  * :meth:`diff`     calls the ``git_branch_diff`` tool synchronously.

Construct once per workspace; reuse across calls.

Example:

    from companybrain.sdk import CompanyBrain

    brain = CompanyBrain(repo="/path/to/repo", workspace="dev")
    res = await brain.extract("/v1/orders", method="POST")
    if res.success:
        print(f"extracted {res.entity_count} entities for ${res.cost_usd}")
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import structlog

from companybrain.harness.commands import (
    SlashCommandError,
    load_default_commands,
    parse_and_render,
)
from companybrain.harness.loop import HarnessLoop, HarnessResult
from companybrain.harness.permissions import load_workspace_grants
from companybrain.harness.workspace import Workspace
from companybrain.sdk.models import DiffResult, QueryResponse, RunResult

log = structlog.get_logger(__name__)


class CompanyBrain:
    """Programmatic facade for one workspace.

    Parameters
    ----------
    repo
        Path to the repo. Must exist; need not be a git checkout for
        :meth:`query` to work, but is required for :meth:`extract` and
        :meth:`diff`.
    workspace
        Workspace slug (defaults to ``"dev"`` — the same default the CLI uses).
    workspace_id
        Optional workspace UUID. When omitted the SDK reads it from the
        repo's ``.brain/settings.json`` and finally falls back to the
        ``BRAIN_WORKSPACE_ID`` env var.
    auto_approve
        Skip ASK prompts when True. Defaults to True for SDK callers — the
        embedding context is non-interactive by design and the user knows
        when they wrote ``brain.extract(...)``.
    api_url
        FastAPI base URL when the SDK needs to call the running server (used
        by future P6 features). Defaults to localhost.
    """

    def __init__(
        self,
        *,
        repo: str | Path,
        workspace: str = "dev",
        workspace_id: str | None = None,
        auto_approve: bool = True,
        api_url: str = "http://localhost:8000",
    ):
        self.workspace = Workspace.load(
            repo,
            workspace_id=workspace_id,
            workspace_slug=workspace,
        )
        self.api_url = api_url
        self._auto_approve = bool(auto_approve)
        self._commands = load_default_commands()

    # ── ergonomics ─────────────────────────────────────────────────────────

    @property
    def repo_path(self) -> Path:
        return self.workspace.repo_path

    # ── workflows ──────────────────────────────────────────────────────────

    async def extract(self, endpoint: str, *, method: str = "GET") -> RunResult:
        """Run the canonical extraction pipeline for one endpoint."""
        message = self._render_command("extract", f"{endpoint} {method}")
        result = await self._run(message, command_routed="extract")
        result.entity_count = self._count_brain_entities()
        return result

    async def query(self, question: str) -> QueryResponse:
        """Run a natural-language query and return a structured response."""
        message = self._render_command("query", question)
        run = await self._run(message, command_routed="query")
        return QueryResponse(
            answer=run.final_text,
            citations=_extract_citations(run.final_text),
            cost_usd=run.cost_usd,
            telemetry=run.telemetry,
        )

    async def diff(self, branch_a: str, branch_b: str) -> DiffResult:
        """List source files changed between two refs.

        Bypasses the harness loop — calls ``git_branch_diff`` directly so the
        SDK keeps cost predictable for what is fundamentally a ``git diff``.
        """
        from companybrain.harness.tools import TOOL_REGISTRY

        tool = TOOL_REGISTRY.get("git_branch_diff")
        if tool is None:
            raise RuntimeError("git_branch_diff tool is not registered")
        result = await tool.invoke(
            {"branch_a": branch_a, "branch_b": branch_b},
            context=self.workspace.to_context(),
        )
        files = list(result.get("files", [])) if isinstance(result, dict) else []
        return DiffResult(branch_a=branch_a, branch_b=branch_b, files=files)

    # ── implementation details ─────────────────────────────────────────────

    def _render_command(self, name: str, args: str) -> str:
        try:
            rendered, cmd = parse_and_render(
                f"/{name} {args}".rstrip(),
                registry=self._commands,
            )
            if cmd != name:
                # Defensive — should never happen with our own input.
                raise SlashCommandError(f"command parser routed {name!r} → {cmd!r}")
            return rendered
        except SlashCommandError as exc:
            raise ValueError(f"SDK could not render /{name}: {exc}") from exc

    async def _run(self, message: str, *, command_routed: str | None) -> RunResult:
        grants = load_workspace_grants(self.workspace.id)
        loop = HarnessLoop(
            permissions=grants,
            interactive=False,
            auto_approve=self._auto_approve,
            hook_repo_path=str(self.workspace.repo_path),
        )
        ctx = self.workspace.to_context()
        try:
            harness_result: HarnessResult = await loop.run(message, context=ctx)
        except Exception as exc:  # noqa: BLE001
            log.exception("sdk.harness_failed", endpoint=ctx.get("endpoint_path"))
            return RunResult(success=False, final_text=f"ERROR: {exc}",
                              command_routed=command_routed)
        success = bool(harness_result.final_text)
        return RunResult(
            success=success,
            final_text=harness_result.final_text,
            iterations=harness_result.iterations,
            tool_calls_total=harness_result.tool_call_count,
            tool_calls_ok=harness_result.succeeded_tool_calls,
            cost_usd=float(harness_result.cost.get("total_cost_usd", 0.0)),
            skill_loaded=harness_result.telemetry.get("skill_loaded"),
            brain_md_loaded=bool(harness_result.telemetry.get("brain_md_loaded")),
            telemetry=harness_result.telemetry,
            command_routed=command_routed,
        )

    def _count_brain_entities(self) -> int:
        """Cheap on-disk count of `.brain/<type>/*.json` files."""
        brain = self.workspace.repo_path / ".brain"
        if not brain.is_dir():
            return 0
        total = 0
        for sub in brain.iterdir():
            if sub.is_dir():
                total += sum(1 for _ in sub.glob("*.json"))
        return total


def _extract_citations(text: str) -> list[str]:
    """Pull URN-shaped citations out of a free-text answer."""
    if not text:
        return []
    return sorted({tok for tok in _split_tokens(text) if tok.startswith("urn:cb:")})


def _split_tokens(text: str) -> list[str]:
    """Best-effort word split that keeps URNs together."""
    out: list[str] = []
    current: list[str] = []
    for ch in text:
        if ch.isspace() or ch in ",;()[]{}":
            if current:
                out.append("".join(current))
                current = []
        else:
            current.append(ch)
    if current:
        out.append("".join(current))
    return out


# Synchronous helpers for callers who prefer not to manage their own loop.
def extract_sync(repo: str | Path, endpoint: str, *, method: str = "GET",
                  workspace: str = "dev") -> RunResult:
    """Convenience wrapper: ``asyncio.run(brain.extract(...))``."""
    brain = CompanyBrain(repo=repo, workspace=workspace)
    return asyncio.run(brain.extract(endpoint, method=method))


def query_sync(repo: str | Path, question: str, *, workspace: str = "dev") -> QueryResponse:
    """Convenience wrapper: ``asyncio.run(brain.query(...))``."""
    brain = CompanyBrain(repo=repo, workspace=workspace)
    return asyncio.run(brain.query(question))


__all__ = ["CompanyBrain", "extract_sync", "query_sync"]
