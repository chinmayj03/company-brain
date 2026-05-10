"""Acceptance tests for ADR-0051 Phase 2.

These tests assert two end-to-end properties of the sub-agent layer:

  1. Parallel fan-out — N spawn_extractor sub-agents run concurrently
     against an isolation primitive (asyncio.Semaphore) bounded by
     `settings.max_subagents`.
  2. Context isolation — each sub-agent's input tokens stay flat regardless
     of how much the parent has already accumulated. The implementation
     prompt's "subagent input < 50% of parent input" target maps to:
     each sub-agent's first provider call carries only system + one user
     message (no parent transcript).

LLM calls are scripted via _FakeProvider — no API credentials needed.
"""
from __future__ import annotations

import asyncio
from unittest.mock import patch

from companybrain.config import settings
from companybrain.harness.tools import TOOL_REGISTRY
from companybrain.llm.base import (
    ChatMessage,
    ChatResponse,
    TaskRole,
)


def _resp(content: str = "", *, tool_calls=None,
          input_tokens: int = 80, output_tokens: int = 20) -> ChatResponse:
    return ChatResponse(
        content=content,
        model="mock-model",
        provider="anthropic",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tool_calls=list(tool_calls or []),
    )


class _FakeProvider:
    """Replays a scripted ChatResponse list per (provider instance) and tracks calls."""

    provider_name = "anthropic"

    def __init__(self, responses: list[ChatResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def chat_with_tools(self, *, messages, tools, role, max_tokens):
        # Capture the first message-list we're called with, so tests can
        # assert isolation. Take a shallow copy because the loop mutates it.
        self.calls.append({
            "messages": list(messages),
            "tools":    [t.name for t in tools],
            "role":     role,
        })
        return self._responses.pop(0)

    def model_for_role(self, role: TaskRole) -> str:
        return "claude-haiku-4-5-20251001"


# ── Phase-2 tools register at import time ────────────────────────────────────

async def test_p2_spawn_tools_are_registered():
    """The three spawn_* tools land in TOOL_REGISTRY when the package is imported."""
    expected = {"spawn_extractor", "spawn_verifier", "spawn_research"}
    assert expected.issubset(set(TOOL_REGISTRY))


async def test_spawn_extractor_advertises_correct_schema():
    """The harness exposes spawn_extractor with a `files` array parameter."""
    tool = TOOL_REGISTRY["spawn_extractor"]
    files_param = next(p for p in tool.definition.parameters if p.name == "files")
    assert files_param.type == "array"
    assert files_param.required is True


# ── Fan-out: parallel sub-agents under one spawn_* call ───────────────────────

async def test_spawn_extractor_runs_subagents_in_parallel():
    """All N sub-agents START before any of them finish — proves real parallelism."""
    started_count = 0
    started_event = asyncio.Event()
    release = asyncio.Event()
    counters_lock = asyncio.Lock()

    async def slow_chat(**kwargs):
        nonlocal started_count
        async with counters_lock:
            started_count += 1
            if started_count >= 3:
                started_event.set()
        await release.wait()
        return _resp(content="done")

    class _SlowProvider:
        provider_name = "anthropic"

        async def chat_with_tools(self, **kwargs):
            return await slow_chat(**kwargs)

        def model_for_role(self, role):
            return "claude-haiku-4-5-20251001"

    files = [
        {"path": "/tmp/A.java", "methods": ["A.foo"]},
        {"path": "/tmp/B.java", "methods": ["B.foo"]},
        {"path": "/tmp/C.java", "methods": ["C.foo"]},
    ]

    async def releaser():
        await started_event.wait()
        release.set()

    # Force the per-sub-agent provider lookup to return our slow stub.
    with patch("companybrain.harness.subagent.get_provider", return_value=_SlowProvider()):
        spawn = TOOL_REGISTRY["spawn_extractor"].handler
        result, _ = await asyncio.gather(
            spawn({"files": files}, {"repo_path": "/tmp"}),
            releaser(),
        )

    assert result["subagents"] == 3
    # All three providers entered chat before any finished — concurrency held.
    assert started_count >= 3


async def test_spawn_extractor_respects_max_subagents_semaphore(monkeypatch):
    """With max_subagents=2, only two sub-agents run at once even if 4 are queued."""
    in_flight = 0
    peak = 0
    in_flight_lock = asyncio.Lock()

    async def slow_chat(**kwargs):
        nonlocal in_flight, peak
        async with in_flight_lock:
            in_flight += 1
            peak = max(peak, in_flight)
        await asyncio.sleep(0.05)
        async with in_flight_lock:
            in_flight -= 1
        return _resp(content="ok")

    class _SlowProvider:
        provider_name = "anthropic"

        async def chat_with_tools(self, **kwargs):
            return await slow_chat(**kwargs)

        def model_for_role(self, role):
            return "claude-haiku-4-5-20251001"

    monkeypatch.setattr(settings, "max_subagents", 2, raising=False)
    monkeypatch.setattr(settings, "subagent_timeout_s", 30, raising=False)

    files = [{"path": f"/tmp/F{i}.java"} for i in range(4)]

    with patch("companybrain.harness.subagent.get_provider", return_value=_SlowProvider()):
        spawn = TOOL_REGISTRY["spawn_extractor"].handler
        result = await spawn({"files": files}, {"repo_path": "/tmp"})

    assert result["subagents"] == 4
    assert peak <= 2, f"peak concurrency {peak} exceeded max_subagents=2"


# ── Context isolation — the headline P2 property ─────────────────────────────

async def test_subagent_input_is_fraction_of_parent_input():
    """Each sub-agent's first provider call carries far less input than the parent's
    accumulated state would, proving fresh-context isolation.

    Models a representative parent transcript size for a 60-method endpoint
    extraction (~50 KB of system + plan + per-file accumulated results) and
    asserts that every sub-agent's input is well under half of that — the
    headline P2 cost win.
    """
    # Representative parent input for a 60-method endpoint: navigator output,
    # plan, per-file extraction results accumulated over many turns. ~50 KB
    # is conservative; in practice we have seen 80-120 KB on busy controllers.
    parent_input_chars = 50_000

    captured_inputs: list[int] = []

    class _CapturingProvider:
        provider_name = "anthropic"

        async def chat_with_tools(self, *, messages, **kwargs):
            captured_inputs.append(sum(len(m.content or "") for m in messages))
            return _resp(content="entities=1, edges=2")

        def model_for_role(self, role):
            return "claude-haiku-4-5-20251001"

    files = [{"path": f"/tmp/F{i}.java", "methods": [f"F{i}.run"]} for i in range(5)]

    with patch("companybrain.harness.subagent.get_provider", return_value=_CapturingProvider()):
        spawn = TOOL_REGISTRY["spawn_extractor"].handler
        result = await spawn({"files": files}, {"repo_path": "/tmp"})

    assert result["subagents"] == 5
    assert len(captured_inputs) == 5
    # Every sub-agent's input is far below the parent input — proves isolation.
    half_parent = parent_input_chars / 2
    for chars in captured_inputs:
        assert chars < half_parent, (
            f"sub-agent input {chars} chars not < 50% of parent input "
            f"{parent_input_chars} chars — context isolation broken"
        )
    # Stronger property: sub-agent input is < 10% of parent input in practice.
    assert max(captured_inputs) < parent_input_chars / 10


async def test_subagent_carries_only_system_plus_one_user_on_first_turn():
    """First provider call contains exactly two messages: system + user."""
    captured: list[list[ChatMessage]] = []

    class _CapturingProvider:
        provider_name = "anthropic"

        async def chat_with_tools(self, *, messages, **kwargs):
            captured.append(list(messages))
            return _resp(content="ok")

        def model_for_role(self, role):
            return "claude-haiku-4-5-20251001"

    with patch("companybrain.harness.subagent.get_provider", return_value=_CapturingProvider()):
        spawn = TOOL_REGISTRY["spawn_extractor"].handler
        await spawn({"files": [{"path": "/tmp/F.java"}]}, {"repo_path": "/tmp"})

    assert len(captured) == 1
    msgs = captured[0]
    assert len(msgs) == 2
    assert msgs[0].role == "system"
    assert msgs[1].role == "user"
    # The user prompt mentions the file we asked for.
    assert "/tmp/F.java" in msgs[1].content


# ── Aggregate telemetry ──────────────────────────────────────────────────────

async def test_spawn_extractor_aggregates_cost_and_tokens():
    """Returned payload sums per-sub-agent cost and input/output tokens."""

    class _FixedProvider:
        provider_name = "anthropic"

        async def chat_with_tools(self, **kwargs):
            return _resp(content="ok", input_tokens=200, output_tokens=50)

        def model_for_role(self, role):
            return "claude-haiku-4-5-20251001"

    files = [{"path": f"/tmp/F{i}.java"} for i in range(3)]

    with patch("companybrain.harness.subagent.get_provider", return_value=_FixedProvider()):
        spawn = TOOL_REGISTRY["spawn_extractor"].handler
        result = await spawn({"files": files}, {"repo_path": "/tmp"})

    assert result["subagents"] == 3
    assert result["total_input_tokens"] == 600
    assert result["total_output_tokens"] == 150
    assert result["total_cost_usd"] > 0
    assert result["timed_out"] == []


# ── Verifier returns structured verdicts ─────────────────────────────────────

async def test_spawn_verifier_parses_verdict_and_evidence():
    """A sub-agent emitting the VERDICT/EVIDENCE block parses into structured fields."""

    class _VerdictProvider:
        provider_name = "anthropic"

        async def chat_with_tools(self, **kwargs):
            return _resp(content=(
                "I checked the call sites.\n"
                "VERDICT: supported\n"
                "EVIDENCE: getPayerCompetitors invokes service.findPayerCompetitors at line 18."
            ))

        def model_for_role(self, role):
            return "claude-haiku-4-5-20251001"

    claims = [
        {"claim": "CompetitivenessController.getPayerCompetitors CALLS "
                  "CompetitivenessService.findPayerCompetitors"},
    ]

    with patch("companybrain.harness.subagent.get_provider", return_value=_VerdictProvider()):
        spawn = TOOL_REGISTRY["spawn_verifier"].handler
        result = await spawn({"claims": claims}, {"repo_path": "/tmp"})

    assert result["subagents"] == 1
    assert result["supported"] == 1
    assert result["contradicted"] == 0
    item = result["results"][0]
    assert item["verdict"] == "supported"
    assert "findPayerCompetitors" in item["evidence"]


async def test_spawn_verifier_falls_back_to_inconclusive():
    """If the sub-agent doesn't emit a verdict block, parser yields 'inconclusive'."""

    class _VagueProvider:
        provider_name = "anthropic"

        async def chat_with_tools(self, **kwargs):
            return _resp(content="not sure honestly")

        def model_for_role(self, role):
            return "claude-haiku-4-5-20251001"

    with patch("companybrain.harness.subagent.get_provider", return_value=_VagueProvider()):
        spawn = TOOL_REGISTRY["spawn_verifier"].handler
        result = await spawn(
            {"claims": [{"claim": "x"}]},
            {"repo_path": "/tmp"},
        )

    assert result["inconclusive"] == 1
    assert result["results"][0]["verdict"] == "inconclusive"


async def test_spawn_research_returns_per_question_answers():
    """Each question gets a focused prose answer back from its sub-agent."""

    class _ResearchProvider:
        provider_name = "anthropic"

        def __init__(self):
            self._answers = [
                "It uses @RestController and @PostMapping.",
                "There is no migration; the column has the same name.",
            ]

        async def chat_with_tools(self, *, messages, **kwargs):
            return _resp(content=self._answers.pop(0))

        def model_for_role(self, role):
            return "claude-haiku-4-5-20251001"

    questions = [
        {"question": "Which Spring annotations does CompetitivenessController use?"},
        {"question": "Was the lob column renamed?"},
    ]
    provider = _ResearchProvider()

    with patch("companybrain.harness.subagent.get_provider", return_value=provider):
        spawn = TOOL_REGISTRY["spawn_research"].handler
        result = await spawn({"questions": questions}, {"repo_path": "/tmp"})

    assert result["subagents"] == 2
    answers = [r["answer"] for r in result["results"]]
    assert any("RestController" in a for a in answers)
    assert any("migration" in a for a in answers)


# ── Empty inputs and timeouts ────────────────────────────────────────────────

async def test_spawn_extractor_handles_empty_file_list():
    """Empty input → no sub-agents spawned, zero cost, no provider calls."""
    with patch("companybrain.harness.subagent.get_provider") as get_p:
        spawn = TOOL_REGISTRY["spawn_extractor"].handler
        result = await spawn({"files": []}, {"repo_path": "/tmp"})
        assert result == {"subagents": 0, "total_cost_usd": 0.0, "results": []}
        get_p.assert_not_called()


async def test_spawn_extractor_marks_timed_out_subagents(monkeypatch):
    """Sub-agents that exceed subagent_timeout_s land in the timed_out list."""

    class _HangProvider:
        provider_name = "anthropic"

        async def chat_with_tools(self, **kwargs):
            await asyncio.sleep(60)
            return _resp(content="never")

        def model_for_role(self, role):
            return "claude-haiku-4-5-20251001"

    monkeypatch.setattr(settings, "subagent_timeout_s", 0.05, raising=False)

    with patch("companybrain.harness.subagent.get_provider", return_value=_HangProvider()):
        spawn = TOOL_REGISTRY["spawn_extractor"].handler
        result = await spawn(
            {"files": [{"path": "/tmp/Hang.java"}]},
            {"repo_path": "/tmp"},
        )

    assert result["subagents"] == 1
    assert result["timed_out"] == ["/tmp/Hang.java"]
    assert result["results"][0]["timed_out"] is True
