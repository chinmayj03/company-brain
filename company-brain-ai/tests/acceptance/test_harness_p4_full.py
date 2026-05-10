"""Acceptance tests for ADR-0051 Phase 4 — hooks, permissions, streaming, cost.

These tests assert the headline P4 properties end-to-end against a fake LLM
provider so the suite runs deterministically without API credentials:

  1. A pre_extraction hook fires when extract_methods_from_class is dispatched
     and the firing is reflected in `HarnessResult.telemetry["hook_invocations"]`.
  2. The permissions table can DENY a tool; the harness surfaces the denial as
     a tool-result error (the model can re-plan) and never invokes the handler.
  3. The TodoList streams add/update events through SSE in the order the
     harness mutates them.
  4. Per-tool cost telemetry credits the right tool when a sub-agent fan-out
     reports its aggregate cost.
  5. Compaction trims the message history when the running input usage
     crosses the configured threshold and increments the telemetry counter.
"""
from __future__ import annotations

import stat
from pathlib import Path

import pytest
from companybrain.harness import session as session_mod
from companybrain.harness.loop import HarnessLoop
from companybrain.harness.permissions import (
    Capability,
    WorkspaceGrants,
)
from companybrain.harness.progress import TodoList, TodoStatus
from companybrain.harness.tools import TOOL_REGISTRY, register_tool
from companybrain.llm.base import (
    ChatResponse,
    TaskRole,
    ToolCall,
)
from fastapi.testclient import TestClient

# ── fake provider helpers ──────────────────────────────────────────────────


def _resp(
    content: str = "",
    *,
    tool_calls=None,
    in_tok: int = 100,
    out_tok: int = 30,
) -> ChatResponse:
    return ChatResponse(
        content=content,
        model="fake/balanced",
        provider="anthropic",
        input_tokens=in_tok,
        output_tokens=out_tok,
        tool_calls=list(tool_calls or []),
    )


class _ScriptedProvider:
    """Replays a list of ChatResponse objects in order, for a deterministic loop."""

    provider_name = "anthropic"

    def __init__(self, responses: list[ChatResponse]):
        self._responses = list(responses)
        self.call_count = 0

    async def chat_with_tools(self, **kwargs):
        if self.call_count >= len(self._responses):
            return _resp("done")
        r = self._responses[self.call_count]
        self.call_count += 1
        return r

    def model_for_role(self, role: TaskRole) -> str:
        return "claude-sonnet-4-6"


def _executable(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# ── 1. pre_extraction hook fires + telemetry counts it ─────────────────────


async def test_pre_extraction_hook_runs_and_telemetry_records_it(tmp_path: Path):
    """Wire a pre_extraction hook; the loop should fire it once + count it.

    The harness maps `extract_methods_from_class` → ``pre_extraction`` /
    ``post_extraction``. Real extraction needs heavy fixtures, so we
    monkey-swap the registry entry's handler in place — preserving the tool
    name (so the event map still fires) while keeping the test fast.
    """
    from companybrain.harness.tools import Tool as _Tool

    saved = TOOL_REGISTRY["extract_methods_from_class"]

    async def _stub_handler(args, context):
        return [{"qname": "X.foo", "edges": []}]

    TOOL_REGISTRY["extract_methods_from_class"] = _Tool(
        definition=saved.definition,
        handler=_stub_handler,
        requires=saved.requires,
    )

    try:
        _executable(
            tmp_path / ".brain" / "hooks" / "pre_extraction.sh",
            "#!/bin/sh\ncat >/dev/null\necho '{\"drop_globs\": [\"*_test.java\"]}'\n",
        )

        provider = _ScriptedProvider([
            _resp(tool_calls=[ToolCall(
                name="extract_methods_from_class",
                arguments={"file": "X.java", "methods": ["X.foo"]},
                call_id="c1",
            )]),
            _resp(content="all done"),
        ])
        loop = HarnessLoop(
            provider=provider,
            max_iterations=3,
            hook_repo_path=str(tmp_path),
            enable_hooks=True,
        )
        result = await loop.run(
            "Extract endpoint X.",
            context={
                "repo_path": str(tmp_path),
                "workspace_id": "ws",
                "endpoint_path": "/x", "http_method": "GET",
            },
        )

        assert result.final_text == "all done"
        invocations = result.telemetry["hook_invocations"]
        assert invocations.get("pre_extraction") == 1
        # post_extraction has no script installed, so it shouldn't be counted.
        assert "post_extraction" not in invocations
    finally:
        TOOL_REGISTRY["extract_methods_from_class"] = saved


# ── 2. permissions can deny a tool ─────────────────────────────────────────


async def test_permission_deny_short_circuits_dispatch():
    """A DENY decision short-circuits the call; the handler is never invoked."""
    invoked = False

    @register_tool(
        name="_p4_denied",
        description="should never run",
        parameters=[],
        requires=(Capability.EXEC_SHELL,),
    )
    async def _handler(args, context):
        nonlocal invoked
        invoked = True
        return {"ran": True}

    try:
        # Default grants deny exec_shell. With permissions enforced, this
        # tool's handler must not run.
        grants = WorkspaceGrants.from_settings({"exec_shell": "deny"})
        provider = _ScriptedProvider([
            _resp(tool_calls=[ToolCall(name="_p4_denied", arguments={}, call_id="c1")]),
            _resp(content="ok, gave up"),
        ])
        loop = HarnessLoop(
            provider=provider, max_iterations=3,
            permissions=grants,
        )
        result = await loop.run("go", context={})

        assert invoked is False
        assert result.tool_calls[0]["ok"] is False
        assert "denied" in (result.tool_calls[0]["error"] or "")
        assert result.final_text == "ok, gave up"
    finally:
        TOOL_REGISTRY.pop("_p4_denied", None)


async def test_permission_ask_non_interactive_denies():
    """A non-interactive run with no auto-approve converts ASK → DENY."""
    @register_tool(
        name="_p4_writes",
        description="needs write_brain",
        parameters=[],
        requires=(Capability.WRITE_BRAIN,),
    )
    async def _handler(args, context):
        return {"ran": True}

    try:
        grants = WorkspaceGrants.from_settings({"write_brain": "ask"})
        provider = _ScriptedProvider([
            _resp(tool_calls=[ToolCall(name="_p4_writes", arguments={}, call_id="c1")]),
            _resp(content="ok"),
        ])
        loop = HarnessLoop(
            provider=provider, max_iterations=3,
            permissions=grants, interactive=False, auto_approve=False,
        )
        result = await loop.run("go", context={})
        assert result.tool_calls[0]["ok"] is False
        assert "denied" in (result.tool_calls[0]["error"] or "")
    finally:
        TOOL_REGISTRY.pop("_p4_writes", None)


async def test_permission_ask_with_auto_approve_runs():
    """With auto_approve set, ASK collapses to AUTO and the handler runs."""
    invoked = False

    @register_tool(
        name="_p4_autowrites",
        description="needs write_brain",
        parameters=[],
        requires=(Capability.WRITE_BRAIN,),
    )
    async def _handler(args, context):
        nonlocal invoked
        invoked = True
        return {"ok": True}

    try:
        grants = WorkspaceGrants.from_settings({"write_brain": "ask"})
        provider = _ScriptedProvider([
            _resp(tool_calls=[ToolCall(name="_p4_autowrites", arguments={}, call_id="c1")]),
            _resp(content="done"),
        ])
        loop = HarnessLoop(
            provider=provider, max_iterations=3,
            permissions=grants, interactive=False, auto_approve=True,
        )
        result = await loop.run("go", context={})
        assert invoked is True
        assert result.tool_calls[0]["ok"] is True
    finally:
        TOOL_REGISTRY.pop("_p4_autowrites", None)


# ── 3. TodoList streams events for each tool call ──────────────────────────


async def test_todo_list_records_tool_call_lifecycle():
    """Each dispatched tool produces add → in_progress → completed events."""
    @register_tool(
        name="_p4_todo",
        description="todo test tool",
        parameters=[],
    )
    async def _handler(args, context):
        return {"x": 1}

    events: list[tuple[str, dict]] = []

    try:
        todo = TodoList()
        todo.subscribe(lambda action, item: events.append((action, item)))

        provider = _ScriptedProvider([
            _resp(tool_calls=[ToolCall(name="_p4_todo", arguments={}, call_id="c1")]),
            _resp(content="ok"),
        ])
        loop = HarnessLoop(provider=provider, max_iterations=3, todo=todo)
        await loop.run("go", context={})

        # add (pending) → update (in_progress) → update (completed)
        assert len(events) == 3
        assert events[0][0] == "add"
        assert events[0][1]["status"] == TodoStatus.PENDING.value
        assert events[1][0] == "update"
        assert events[1][1]["status"] == TodoStatus.IN_PROGRESS.value
        assert events[2][0] == "update"
        assert events[2][1]["status"] == TodoStatus.COMPLETED.value
    finally:
        TOOL_REGISTRY.pop("_p4_todo", None)


async def test_todo_list_marks_tool_as_failed_on_exception():
    @register_tool(
        name="_p4_todo_fail",
        description="todo failure test",
        parameters=[],
    )
    async def _handler(args, context):
        raise RuntimeError("boom")

    try:
        todo = TodoList()
        events: list[tuple[str, dict]] = []
        todo.subscribe(lambda action, item: events.append((action, item)))

        provider = _ScriptedProvider([
            _resp(tool_calls=[ToolCall(name="_p4_todo_fail", arguments={}, call_id="c1")]),
            _resp(content="recovered"),
        ])
        loop = HarnessLoop(provider=provider, max_iterations=3, todo=todo)
        await loop.run("go", context={})

        statuses = [e[1]["status"] for e in events]
        assert statuses[-1] == TodoStatus.FAILED.value
    finally:
        TOOL_REGISTRY.pop("_p4_todo_fail", None)


# ── 4. SSE endpoint round-trips TodoList events ────────────────────────────


def test_sse_endpoint_streams_snapshot_and_events():
    """A registered session whose TodoList mutates emits SSE lines for each event."""
    # Build a session, mutate its TodoList, then read the SSE stream — the
    # client's first event should be the snapshot, then the subsequent
    # mutations as add/update events.
    sess = session_mod.create(
        id="sess-test-sse",
        endpoint="/x", method="GET", repo_path="/tmp", workspace_id="ws",
    )
    try:
        from companybrain.harness.progress import TodoItem, TodoStatus

        # Pre-populate one item so the snapshot has content.
        sess.todo.add(TodoItem(id="i1", title="prep"))
        sess.todo.update("i1", status=TodoStatus.COMPLETED)
        # Mark the session terminal so the SSE generator closes promptly.
        sess.status = "completed"

        # Lazy-import the FastAPI app so the SSE route is mounted.
        from companybrain.api.main import app
        client = TestClient(app)

        with client.stream("GET", f"/pipeline/jobs/{sess.id}/stream") as resp:
            assert resp.status_code == 200
            body = ""
            for chunk in resp.iter_text():
                body += chunk
                if "[DONE]" in body:
                    break

        # Snapshot must be the first event; final event is [DONE].
        assert "\"action\": \"snapshot\"" in body
        assert "[DONE]" in body
        assert "\"id\": \"i1\"" in body
    finally:
        session_mod.remove(sess.id)


def test_sse_endpoint_returns_404_for_unknown_session():
    from companybrain.api.main import app
    client = TestClient(app)
    resp = client.get("/pipeline/jobs/does-not-exist/stream")
    assert resp.status_code == 404


# ── 5. per-tool cost attribution ───────────────────────────────────────────


async def test_cost_tracker_attributes_subagent_cost_to_spawn_tool():
    """When a tool returns total_cost_usd, it's credited under that tool's name."""
    @register_tool(
        name="_p4_spawn",
        description="fake spawn tool",
        parameters=[],
    )
    async def _handler(args, context):
        return {
            "subagents": 4,
            "total_cost_usd": 0.0734,
            "total_input_tokens":  3200,
            "total_output_tokens": 600,
            "results": [{"file": f"f{i}.java"} for i in range(4)],
        }

    try:
        provider = _ScriptedProvider([
            _resp(tool_calls=[ToolCall(name="_p4_spawn", arguments={}, call_id="c1")]),
            _resp(content="finished"),
        ])
        loop = HarnessLoop(provider=provider, max_iterations=3)
        result = await loop.run("go", context={})

        cost = result.telemetry["cost"]
        assert "_p4_spawn" in cost["by_tool"]
        assert cost["by_tool"]["_p4_spawn"]["cost_usd"] == pytest.approx(0.0734, rel=1e-3)
        # Loop cost (the assistant's own LLM calls) is also accumulated.
        assert "_loop" in cost["by_tool"]
        assert cost["total_cost_usd"] >= cost["by_tool"]["_p4_spawn"]["cost_usd"]
    finally:
        TOOL_REGISTRY.pop("_p4_spawn", None)


# ── 6. compaction triggers when usage crosses the threshold ────────────────


async def test_compaction_triggers_when_input_usage_crosses_threshold():
    """A series of high-usage assistant turns should trigger one or more compactions."""
    @register_tool(
        name="_p4_chatty",
        description="cheap stub",
        parameters=[],
    )
    async def _handler(args, context):
        return {"ok": True}

    try:
        # Each response reports very high token usage so we cross the
        # threshold within ~3 turns. Enough turns for the message list to
        # exceed MIN_MESSAGES_TO_COMPACT.
        many_calls = [
            _resp(in_tok=80_000, out_tok=2_000,
                  tool_calls=[ToolCall(name="_p4_chatty", arguments={}, call_id=f"c{i}")])
            for i in range(8)
        ]
        many_calls.append(_resp(content="finally done"))

        provider = _ScriptedProvider(many_calls)
        loop = HarnessLoop(
            provider=provider,
            max_iterations=20,
            compaction_threshold=0.50,            # easier to trip in tests
            context_limit_tokens=200_000,
        )
        result = await loop.run("go", context={})

        assert result.telemetry["compaction_invocations"] >= 1
        assert result.telemetry["max_context_used"] > 0
    finally:
        TOOL_REGISTRY.pop("_p4_chatty", None)
