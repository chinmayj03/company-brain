"""Unit tests for the HarnessLoop (ADR-0051 P1).

The loop owns conversation history and tool dispatch. Real LLM calls are
mocked — we drive the loop with deterministic ChatResponse sequences and
assert it dispatches/terminates correctly.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from companybrain.harness.loop import HarnessLoop, HarnessResult, _to_str
from companybrain.harness.system_prompt import build_system_prompt
from companybrain.harness.tools import TOOL_REGISTRY, register_tool
from companybrain.llm.base import (
    ChatMessage, ChatResponse, TaskRole, ToolCall, ToolParameter,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_response(*, content: str = "", tool_calls=None) -> ChatResponse:
    return ChatResponse(
        content=content,
        model="mock-model",
        provider="mock",
        tool_calls=list(tool_calls or []),
    )


class _FakeProvider:
    """Minimal LLMProvider stand-in that replays a scripted ChatResponse list."""

    provider_name = "mock"

    def __init__(self, responses: list[ChatResponse]):
        self.chat_with_tools = AsyncMock(side_effect=responses)

    def model_for_role(self, role: TaskRole) -> str:
        return f"mock/{role.value}"


def _make_loop(provider_responses: list[ChatResponse], **kwargs) -> HarnessLoop:
    """Build a HarnessLoop whose provider returns the given responses in order."""
    return HarnessLoop(provider=_FakeProvider(provider_responses), **kwargs)


# ── Loop control flow ────────────────────────────────────────────────────────

async def test_loop_returns_text_when_no_tool_calls():
    """Bare text response → loop returns it as final_text after one iteration."""
    loop = _make_loop([_make_response(content="all done")])
    result = await loop.run("do the thing", context={})

    assert isinstance(result, HarnessResult)
    assert result.final_text == "all done"
    assert result.iterations == 1
    assert result.tool_call_count == 0
    assert result.telemetry["provider"] == "mock"
    assert result.telemetry["iterations"] == 1


async def test_loop_dispatches_one_tool_then_returns():
    """Tool call in turn 1 → loop dispatches, then text in turn 2 wins."""
    @register_tool(
        name="_test_echo",
        description="Echo arg back.",
        parameters=[ToolParameter("msg", "string", "what to echo")],
    )
    async def _echo(args, context):
        return {"echoed": args["msg"]}

    try:
        loop = _make_loop([
            _make_response(tool_calls=[ToolCall(name="_test_echo",
                                                arguments={"msg": "hi"},
                                                call_id="call_1")]),
            _make_response(content="finished"),
        ])
        result = await loop.run("go", context={})

        assert result.iterations == 2
        assert result.final_text == "finished"
        assert result.tool_call_count == 1
        assert result.tool_calls[0]["name"] == "_test_echo"
        assert result.tool_calls[0]["ok"] is True
        # Provider saw the tool result on its second call.
        second_call_messages = loop._provider.chat_with_tools.call_args_list[1].kwargs["messages"]
        tool_msgs = [m for m in second_call_messages if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert "hi" in tool_msgs[0].content
    finally:
        TOOL_REGISTRY.pop("_test_echo", None)


async def test_loop_runs_parallel_tool_calls_in_one_turn():
    """Multiple tool_calls in one assistant turn → harness fans out via gather."""
    started = asyncio.Event()
    release = asyncio.Event()
    call_order: list[str] = []

    @register_tool(
        name="_test_slow",
        description="Slow tool.",
        parameters=[ToolParameter("idx", "string", "id")],
    )
    async def _slow(args, context):
        call_order.append(f"start:{args['idx']}")
        started.set()
        await release.wait()
        call_order.append(f"done:{args['idx']}")
        return {"idx": args["idx"]}

    try:
        async def releaser():
            await started.wait()
            # Once the FIRST call has started, all parallel calls should have
            # started too (asyncio.gather schedules them concurrently). Release.
            await asyncio.sleep(0)   # let other coros enter the wait
            release.set()

        loop = _make_loop([
            _make_response(tool_calls=[
                ToolCall(name="_test_slow", arguments={"idx": "a"}, call_id="c1"),
                ToolCall(name="_test_slow", arguments={"idx": "b"}, call_id="c2"),
                ToolCall(name="_test_slow", arguments={"idx": "c"}, call_id="c3"),
            ]),
            _make_response(content="done"),
        ])

        # Run loop and releaser concurrently.
        result, _ = await asyncio.gather(
            loop.run("go", context={}),
            releaser(),
        )

        assert result.tool_call_count == 3
        # All three should have STARTED before any of them finished — proves parallel.
        starts = [c for c in call_order if c.startswith("start:")]
        first_done = next(i for i, c in enumerate(call_order) if c.startswith("done:"))
        assert len(starts) == 3
        assert first_done >= 3   # all 3 starts came before the first done
    finally:
        TOOL_REGISTRY.pop("_test_slow", None)


async def test_loop_handles_unknown_tool_gracefully():
    """Unknown tool name → error fed back to model; loop continues."""
    loop = _make_loop([
        _make_response(tool_calls=[
            ToolCall(name="does_not_exist", arguments={}, call_id="x1"),
        ]),
        _make_response(content="recovered"),
    ])
    result = await loop.run("go", context={})

    assert result.tool_calls[0]["ok"] is False
    assert "Unknown tool" in (result.tool_calls[0]["error"] or "")
    assert result.final_text == "recovered"


async def test_loop_surfaces_tool_exception_as_error():
    """Tool raising → surfaced as {error} to the model; loop continues."""
    @register_tool(
        name="_test_boom",
        description="Always raises.",
        parameters=[],
    )
    async def _boom(args, context):
        raise RuntimeError("kaboom")

    try:
        loop = _make_loop([
            _make_response(tool_calls=[ToolCall(name="_test_boom",
                                                arguments={},
                                                call_id="b1")]),
            _make_response(content="ok"),
        ])
        result = await loop.run("go", context={})
        assert result.tool_calls[0]["ok"] is False
        assert "kaboom" in (result.tool_calls[0]["error"] or "")
        assert result.final_text == "ok"
    finally:
        TOOL_REGISTRY.pop("_test_boom", None)


async def test_loop_enforces_tool_timeout():
    """Tool exceeding tool_timeout_seconds → returned as a timeout error."""
    @register_tool(
        name="_test_hang",
        description="Hangs forever.",
        parameters=[],
    )
    async def _hang(args, context):
        await asyncio.sleep(60)

    try:
        loop = _make_loop(
            [
                _make_response(tool_calls=[ToolCall(name="_test_hang",
                                                    arguments={},
                                                    call_id="h1")]),
                _make_response(content="moved on"),
            ],
            tool_timeout_seconds=0.05,
        )
        result = await loop.run("go", context={})
        assert result.tool_calls[0]["ok"] is False
        assert "timed out" in (result.tool_calls[0]["error"] or "")
        assert result.final_text == "moved on"
    finally:
        TOOL_REGISTRY.pop("_test_hang", None)


async def test_loop_caps_at_max_iterations():
    """Model that never stops → loop terminates at max_iterations with last text."""
    # Build N tool-only responses then assert termination at the cap.
    @register_tool(
        name="_test_noop",
        description="Returns nothing.",
        parameters=[],
    )
    async def _noop(args, context):
        return {"ok": True}

    try:
        responses = [
            _make_response(content=f"turn {i}",
                           tool_calls=[ToolCall(name="_test_noop",
                                                arguments={},
                                                call_id=f"n{i}")])
            for i in range(5)
        ]
        loop = _make_loop(responses, max_iterations=3)
        result = await loop.run("go", context={})

        assert result.iterations == 3
        # Salvaged final_text comes from the last assistant turn.
        assert result.final_text.startswith("turn ")
    finally:
        TOOL_REGISTRY.pop("_test_noop", None)


# ── Registry + system prompt ──────────────────────────────────────────────────

def test_registry_rejects_name_collision_from_different_module():
    """Re-registering a name from a different module is a hard error."""
    @register_tool(name="_collide_x",
                   description="first owner",
                   parameters=[])
    async def first(args, context):
        return None

    try:
        with pytest.raises(ValueError, match="collision"):
            # Smuggle a fake __module__ to simulate a different file.
            async def second(args, context):
                return None
            second.__module__ = "another.module"
            register_tool(name="_collide_x", description="second", parameters=[])(second)
    finally:
        TOOL_REGISTRY.pop("_collide_x", None)


def test_registry_rejects_sync_handler():
    """Sync handlers can't be registered — would block the loop's event loop."""
    with pytest.raises(TypeError, match="must be async"):
        @register_tool(name="_sync_handler",
                       description="bad",
                       parameters=[])
        def bad(args, context):  # type: ignore[no-untyped-def]
            return None


def test_system_prompt_lists_all_registered_tools():
    """build_system_prompt mentions every tool currently in the registry."""
    prompt = build_system_prompt({
        "workspace_id":  "ws-test",
        "repo_path":     "/tmp/repo",
        "endpoint_path": "/x",
        "http_method":   "POST",
    })
    for tool_name in TOOL_REGISTRY:
        assert f"- {tool_name}:" in prompt, f"{tool_name} missing from system prompt"
    assert "ws-test" in prompt
    assert "/tmp/repo" in prompt


# ── _to_str helper ────────────────────────────────────────────────────────────

def test_to_str_passes_strings_through():
    assert _to_str("hello") == "hello"


def test_to_str_serialises_dicts_and_lists():
    assert _to_str({"a": 1}) == '{"a": 1}'
    assert _to_str([1, 2]) == "[1, 2]"


def test_to_str_falls_back_for_unserialisable():
    class Weird:
        pass
    out = _to_str(Weird())
    assert "Weird" in out
