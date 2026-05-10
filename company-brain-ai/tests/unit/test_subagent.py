"""Unit tests for Subagent (ADR-0051 P2).

The Subagent runner owns its own conversation history and dispatches a
restricted tool subset. Tests script the LLM responses (no real API calls)
and assert the runner's control flow, allowlist enforcement, telemetry,
and isolation guarantees.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from companybrain.harness.subagent import (
    Subagent,
    SubagentResult,
    run_with_timeout,
)
from companybrain.harness.tools import TOOL_REGISTRY, register_tool
from companybrain.llm.base import (
    ChatResponse,
    TaskRole,
    ToolCall,
    ToolParameter,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _resp(*, content: str = "", tool_calls=None,
          input_tokens: int = 100, output_tokens: int = 30) -> ChatResponse:
    return ChatResponse(
        content=content,
        model="mock-model",
        provider="anthropic",   # picks up haiku pricing for cost test
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tool_calls=list(tool_calls or []),
    )


class _FakeProvider:
    """Replays a scripted ChatResponse list and exposes provider metadata."""

    provider_name = "anthropic"

    def __init__(self, responses: list[ChatResponse]):
        self.chat_with_tools = AsyncMock(side_effect=responses)

    def model_for_role(self, role: TaskRole) -> str:
        return "claude-haiku-4-5-20251001"


def _make(*, responses: list[ChatResponse], allowed: list[str], **kwargs) -> Subagent:
    return Subagent(
        name="t",
        allowed_tools=allowed,
        system_prompt="you are a test sub-agent",
        provider=_FakeProvider(responses),
        **kwargs,
    )


# ── Control flow ──────────────────────────────────────────────────────────────

async def test_subagent_returns_text_when_no_tool_calls():
    """Bare text response → loop returns it as final_text after one iteration."""
    sub = _make(responses=[_resp(content="answer")], allowed=[])
    res = await sub.run("question", context={})

    assert isinstance(res, SubagentResult)
    assert res.final_text == "answer"
    assert res.iterations == 1
    assert res.tool_call_count == 0
    assert res.input_tokens == 100
    assert res.output_tokens == 30
    # Cost > 0 proves compute_cost_usd ran.
    assert res.cost_usd > 0
    assert res.error is None


async def test_subagent_dispatches_one_tool_then_returns():
    """Tool call in turn 1, text in turn 2 → final_text = turn 2."""
    @register_tool(
        name="_sub_echo",
        description="echo arg",
        parameters=[ToolParameter("msg", "string", "what to echo")],
    )
    async def _echo(args, context):
        return {"echoed": args["msg"]}

    try:
        sub = _make(
            responses=[
                _resp(tool_calls=[ToolCall(name="_sub_echo",
                                           arguments={"msg": "hi"},
                                           call_id="c1")]),
                _resp(content="finished"),
            ],
            allowed=["_sub_echo"],
        )
        res = await sub.run("go", context={})

        assert res.iterations == 2
        assert res.final_text == "finished"
        assert res.tool_call_count == 1
        assert res.tool_calls[0]["ok"] is True
        # Sub-agent's second call sees the tool result message.
        second_call = sub._provider.chat_with_tools.call_args_list[1].kwargs["messages"]
        tool_msgs = [m for m in second_call if m.role == "tool"]
        assert len(tool_msgs) == 1
        assert "hi" in tool_msgs[0].content
    finally:
        TOOL_REGISTRY.pop("_sub_echo", None)


async def test_subagent_runs_parallel_tool_calls_in_one_turn():
    """Multiple tool_calls in one turn → fans out via gather."""
    started = asyncio.Event()
    release = asyncio.Event()
    order: list[str] = []

    @register_tool(
        name="_sub_slow",
        description="slow",
        parameters=[ToolParameter("idx", "string", "id")],
    )
    async def _slow(args, context):
        order.append(f"start:{args['idx']}")
        started.set()
        await release.wait()
        order.append(f"done:{args['idx']}")
        return {"idx": args["idx"]}

    try:
        async def releaser():
            await started.wait()
            await asyncio.sleep(0)
            release.set()

        sub = _make(
            responses=[
                _resp(tool_calls=[
                    ToolCall(name="_sub_slow", arguments={"idx": "a"}, call_id="c1"),
                    ToolCall(name="_sub_slow", arguments={"idx": "b"}, call_id="c2"),
                    ToolCall(name="_sub_slow", arguments={"idx": "c"}, call_id="c3"),
                ]),
                _resp(content="done"),
            ],
            allowed=["_sub_slow"],
        )
        res, _ = await asyncio.gather(sub.run("go", context={}), releaser())

        assert res.tool_call_count == 3
        starts = [c for c in order if c.startswith("start:")]
        first_done = next(i for i, c in enumerate(order) if c.startswith("done:"))
        assert len(starts) == 3
        assert first_done >= 3   # all three started before any finished
    finally:
        TOOL_REGISTRY.pop("_sub_slow", None)


# ── Allowlist enforcement ─────────────────────────────────────────────────────

async def test_subagent_blocks_tool_outside_allowlist():
    """Calling a registered-but-not-allowed tool returns an error to the model."""
    @register_tool(
        name="_sub_forbidden",
        description="not allowed",
        parameters=[],
    )
    async def _f(args, context):
        return {"ran": True}

    try:
        sub = _make(
            responses=[
                _resp(tool_calls=[ToolCall(name="_sub_forbidden",
                                           arguments={},
                                           call_id="x1")]),
                _resp(content="ok"),
            ],
            allowed=[],   # empty allowlist
        )
        res = await sub.run("go", context={})

        assert res.tool_calls[0]["ok"] is False
        assert "not in subagent allowlist" in (res.tool_calls[0]["error"] or "")
        assert res.final_text == "ok"
    finally:
        TOOL_REGISTRY.pop("_sub_forbidden", None)


async def test_subagent_only_advertises_allowlisted_tools_to_provider():
    """Schemas sent to the provider include only the allowlist subset."""
    @register_tool(
        name="_adv_a", description="a", parameters=[],
    )
    async def _a(args, context):
        return None

    @register_tool(
        name="_adv_b", description="b", parameters=[],
    )
    async def _b(args, context):
        return None

    try:
        sub = _make(
            responses=[_resp(content="done")],
            allowed=["_adv_a"],
        )
        await sub.run("go", context={})
        sent_tools = sub._provider.chat_with_tools.call_args.kwargs["tools"]
        names = {t.name for t in sent_tools}
        assert "_adv_a" in names
        assert "_adv_b" not in names
    finally:
        TOOL_REGISTRY.pop("_adv_a", None)
        TOOL_REGISTRY.pop("_adv_b", None)


# ── Failure modes ─────────────────────────────────────────────────────────────

async def test_subagent_surfaces_tool_exception():
    """Tool raising → surfaced as {error}; loop continues."""
    @register_tool(
        name="_sub_boom", description="boom", parameters=[],
    )
    async def _boom(args, context):
        raise RuntimeError("kaboom")

    try:
        sub = _make(
            responses=[
                _resp(tool_calls=[ToolCall(name="_sub_boom",
                                           arguments={},
                                           call_id="b1")]),
                _resp(content="recovered"),
            ],
            allowed=["_sub_boom"],
        )
        res = await sub.run("go", context={})
        assert res.tool_calls[0]["ok"] is False
        assert "kaboom" in (res.tool_calls[0]["error"] or "")
        assert res.final_text == "recovered"
    finally:
        TOOL_REGISTRY.pop("_sub_boom", None)


async def test_subagent_enforces_tool_timeout():
    """Tool exceeding tool_timeout_seconds → timeout error."""
    @register_tool(
        name="_sub_hang", description="hang", parameters=[],
    )
    async def _hang(args, context):
        await asyncio.sleep(60)

    try:
        sub = _make(
            responses=[
                _resp(tool_calls=[ToolCall(name="_sub_hang",
                                           arguments={},
                                           call_id="h1")]),
                _resp(content="moved on"),
            ],
            allowed=["_sub_hang"],
            tool_timeout_seconds=0.05,
        )
        res = await sub.run("go", context={})
        assert res.tool_calls[0]["ok"] is False
        assert "timed out" in (res.tool_calls[0]["error"] or "")
        assert res.final_text == "moved on"
    finally:
        TOOL_REGISTRY.pop("_sub_hang", None)


async def test_subagent_caps_at_max_iterations():
    """Model that never stops → terminates at the cap with last assistant text."""
    @register_tool(
        name="_sub_noop", description="noop", parameters=[],
    )
    async def _noop(args, context):
        return {}

    try:
        responses = [
            _resp(content=f"turn {i}",
                  tool_calls=[ToolCall(name="_sub_noop",
                                       arguments={},
                                       call_id=f"n{i}")])
            for i in range(5)
        ]
        sub = _make(responses=responses, allowed=["_sub_noop"], max_iterations=3)
        res = await sub.run("go", context={})

        assert res.iterations == 3
        assert res.final_text.startswith("turn ")
    finally:
        TOOL_REGISTRY.pop("_sub_noop", None)


async def test_subagent_handles_provider_exception():
    """Provider throwing → surfaced on result.error; loop exits cleanly."""

    class _BoomProvider:
        provider_name = "anthropic"

        async def chat_with_tools(self, **kwargs):
            raise RuntimeError("provider down")

        def model_for_role(self, role):
            return "claude-haiku-4-5-20251001"

    sub = Subagent(
        name="t",
        allowed_tools=[],
        system_prompt="x",
        provider=_BoomProvider(),
    )
    res = await sub.run("go", context={})
    assert res.error is not None
    assert "provider down" in res.error
    assert res.final_text == ""


# ── Telemetry ────────────────────────────────────────────────────────────────

async def test_subagent_accrues_token_usage_across_turns():
    """input_tokens / output_tokens sum across every provider call."""
    @register_tool(
        name="_sub_t", description="t", parameters=[],
    )
    async def _t(args, context):
        return {"ok": True}

    try:
        sub = _make(
            responses=[
                _resp(tool_calls=[ToolCall(name="_sub_t",
                                           arguments={},
                                           call_id="t1")],
                      input_tokens=100, output_tokens=20),
                _resp(content="done", input_tokens=120, output_tokens=15),
            ],
            allowed=["_sub_t"],
        )
        res = await sub.run("go", context={})

        assert res.iterations == 2
        assert res.input_tokens == 220
        assert res.output_tokens == 35
        assert res.cost_usd > 0
    finally:
        TOOL_REGISTRY.pop("_sub_t", None)


async def test_subagent_does_not_inherit_caller_history():
    """Sub-agent's first provider call sees only system + user, never extra messages."""
    sub = _make(responses=[_resp(content="x")], allowed=[])
    await sub.run("the prompt", context={})

    msgs = sub._provider.chat_with_tools.call_args.kwargs["messages"]
    assert len(msgs) == 2
    assert msgs[0].role == "system"
    assert msgs[0].content == "you are a test sub-agent"
    assert msgs[1].role == "user"
    assert msgs[1].content == "the prompt"


# ── run_with_timeout helper ──────────────────────────────────────────────────

async def test_run_with_timeout_returns_timed_out_result():
    """Wall-clock cap converts to a SubagentResult, never a raise."""

    class _SleepProvider:
        provider_name = "anthropic"

        async def chat_with_tools(self, **kwargs):
            await asyncio.sleep(60)
            return _resp(content="never")

        def model_for_role(self, role):
            return "claude-haiku-4-5-20251001"

    sub = Subagent(
        name="slow",
        allowed_tools=[],
        system_prompt="x",
        provider=_SleepProvider(),
    )
    res = await run_with_timeout(sub, "go", context={}, timeout_seconds=0.05)
    assert isinstance(res, SubagentResult)
    assert res.timed_out is True
    assert res.error and "exceeded" in res.error
    assert res.final_text == ""


async def test_run_with_timeout_passes_through_normal_result():
    """When the sub-agent finishes inside the cap, result is returned verbatim."""
    sub = _make(responses=[_resp(content="quick")], allowed=[])
    res = await run_with_timeout(sub, "go", context={}, timeout_seconds=5.0)
    assert res.final_text == "quick"
    assert res.timed_out is False
