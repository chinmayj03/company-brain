"""
Unit tests for ExplorationAgent (ADR-0061 E1).

Strategy: mock the LLM provider so tests run without real API keys.
Assert that:
 - Agent fires when confidence == "low"
 - Tools dispatch correctly
 - _run_exploration_agent wires into query response properly
 - Telemetry fields are set on the returned QueryResponse
"""
from __future__ import annotations

import json
import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from companybrain.agents.exploration_agent import (
    ExplorationAgent,
    ExplorationResult,
    _tool_read_file,
    _tool_get_git_log,
    _tool_get_schema,
)
from companybrain.models.query_response import Confidence, QueryResponse


# ── Fixtures ───────────────────────────────────────────────────────────────────

def _make_provider_response(
    content: str = "",
    wants_tool_call: bool = False,
    tool_calls: list | None = None,
):
    resp = MagicMock()
    resp.content = content
    resp.wants_tool_call = wants_tool_call
    resp.tool_calls = tool_calls or []
    return resp


def _tool_call(name: str, **args):
    tc = MagicMock()
    tc.name = name
    tc.arguments = args
    tc.call_id = f"call_{name}"
    return tc


QUESTION = "Which files reference the deprecated `lob_config` constant?"
INITIAL_SUMMARY = "I found some references but I'm not certain."


# ── ExplorationAgent unit tests ────────────────────────────────────────────────

class TestExplorationAgentExplore:
    """Tests for ExplorationAgent.explore()."""

    @pytest.mark.asyncio
    async def test_agent_returns_result_on_first_synthesis(self, tmp_path):
        """Agent stops when LLM returns synthesis without tool calls."""
        findings_json = json.dumps({
            "findings": "Found lob_config in 3 files.",
            "key_entities": ["urn:cb:component:LobConfig"],
            "confidence_boost": "Explicit file list found",
        })
        mock_resp = _make_provider_response(content=findings_json)

        with patch("companybrain.agents.exploration_agent.get_provider") as mock_prov:
            mock_prov.return_value.chat_with_tools = AsyncMock(return_value=mock_resp)
            agent = ExplorationAgent(repo_path=str(tmp_path), workspace_id="ws-1")
            result = await agent.explore(QUESTION, INITIAL_SUMMARY)

        assert isinstance(result, ExplorationResult)
        assert "lob_config" in result.context
        assert result.rounds_taken == 1
        assert result.tool_calls_made == 0
        assert "urn:cb:component:LobConfig" in result.citations

    @pytest.mark.asyncio
    async def test_agent_makes_tool_calls_before_synthesis(self, tmp_path):
        """Agent makes tool calls, then produces synthesis."""
        tool_resp = _make_provider_response(
            wants_tool_call=True,
            tool_calls=[_tool_call("search_entities", query="lob_config")],
        )
        synthesis_json = json.dumps({
            "findings": "Found it in ConfigReader.java.",
            "key_entities": [],
            "confidence_boost": "Direct grep match",
        })
        final_resp = _make_provider_response(content=synthesis_json)

        call_count = 0

        async def _fake_chat(**kwargs):
            nonlocal call_count
            call_count += 1
            return final_resp if call_count > 1 else tool_resp

        with patch("companybrain.agents.exploration_agent.get_provider") as mock_prov:
            mock_prov.return_value.chat_with_tools = _fake_chat
            # Patch search_entities to avoid real Qdrant
            with patch(
                "companybrain.agents.exploration_agent._tool_search_entities",
                return_value="[urn:cb:component:ConfigReader] ConfigReader: reads lob config",
            ):
                agent = ExplorationAgent(repo_path=str(tmp_path), workspace_id="ws-1")
                result = await agent.explore(QUESTION, INITIAL_SUMMARY)

        assert result.tool_calls_made == 1
        assert "ConfigReader" in result.context

    @pytest.mark.asyncio
    async def test_agent_caps_at_max_rounds(self, tmp_path):
        """Agent halts and requests synthesis after MAX_ROUNDS."""
        from companybrain.agents.exploration_agent import MAX_ROUNDS

        always_tool = _make_provider_response(
            wants_tool_call=True,
            tool_calls=[_tool_call("read_file", path="/nonexistent.java")],
        )
        synthesis = _make_provider_response(
            content='{"findings": "timeout", "key_entities": [], "confidence_boost": ""}',
        )

        call_n = 0

        async def _fake_chat(**kwargs):
            nonlocal call_n
            call_n += 1
            # After MAX_ROUNDS tool-call responses, the agent asks for synthesis
            if call_n > MAX_ROUNDS:
                return synthesis
            return always_tool

        with patch("companybrain.agents.exploration_agent.get_provider") as mock_prov:
            mock_prov.return_value.chat_with_tools = _fake_chat
            agent = ExplorationAgent(repo_path=str(tmp_path), workspace_id="ws-1")
            result = await agent.explore(QUESTION, INITIAL_SUMMARY)

        assert result.rounds_taken == MAX_ROUNDS

    @pytest.mark.asyncio
    async def test_prose_output_still_returns_result(self, tmp_path):
        """Agent handles plain-text (non-JSON) synthesis gracefully."""
        prose_resp = _make_provider_response(
            content="I found the config in three places: A, B, and C.",
        )

        with patch("companybrain.agents.exploration_agent.get_provider") as mock_prov:
            mock_prov.return_value.chat_with_tools = AsyncMock(return_value=prose_resp)
            agent = ExplorationAgent(repo_path=str(tmp_path), workspace_id="ws-1")
            result = await agent.explore(QUESTION, INITIAL_SUMMARY)

        assert result.context  # non-empty
        assert result.citations == []


# ── Tool unit tests ────────────────────────────────────────────────────────────

class TestExplorationTools:

    def test_read_file_existing(self, tmp_path):
        f = tmp_path / "Example.java"
        f.write_text("public class Example {}")
        result = _tool_read_file(str(f))
        assert "Example" in result

    def test_read_file_missing(self, tmp_path):
        result = _tool_read_file(str(tmp_path / "ghost.java"))
        assert "error" in result.lower()

    def test_read_file_truncates(self, tmp_path):
        f = tmp_path / "big.java"
        f.write_text("x" * 10_000)
        result = _tool_read_file(str(f), max_chars=100)
        assert "truncated" in result
        assert len(result) < 300

    def test_get_git_log_no_file(self, tmp_path):
        result = _tool_get_git_log(str(tmp_path / "nonexistent.java"))
        # Either "(no commits found)" or an error string — not a traceback
        assert isinstance(result, str)

    def test_get_schema_missing_urn(self, tmp_path):
        (tmp_path / "index.json").write_text("{}")
        result = _tool_get_schema("urn:cb:component:Missing", str(tmp_path))
        assert "not found" in result or "error" in result.lower()

    def test_get_schema_finds_entity(self, tmp_path):
        entity = {"id": "urn:cb:component:Foo", "qualified_name": "Foo", "metadata": {}}
        (tmp_path / "index.json").write_text(json.dumps({"urn:cb:component:Foo": "component/Foo.json"}))
        comp_dir = tmp_path / "component"
        comp_dir.mkdir()
        (comp_dir / "Foo.json").write_text(json.dumps(entity))
        result = _tool_get_schema("urn:cb:component:Foo", str(tmp_path))
        data = json.loads(result)
        assert data["id"] == "urn:cb:component:Foo"


# ── Integration: _run_exploration_agent wires telemetry ───────────────────────

class TestRunExplorationAgentWiring:

    @pytest.mark.asyncio
    async def test_telemetry_set_when_agent_fires(self, tmp_path):
        from companybrain.api.routes.query import _run_exploration_agent
        from companybrain.models.query_response import Confidence, QueryResponse

        low_conf_response = QueryResponse(
            summary="I'm not sure.",
            confidence=Confidence(level="low", rationale="sparse context"),
        )

        exploration_result = ExplorationResult(
            context="## Exploration Findings\n\nFound in ConfigReader.java",
            citations=["urn:cb:component:ConfigReader"],
            rounds_taken=1,
            tool_calls_made=2,
        )

        re_run_json = json.dumps({
            "summary": "ConfigReader.java uses lob_config on line 42.",
            "confidence": {"level": "high", "rationale": "direct file reference found"},
            "affected_entities": [],
            "call_chain": [],
        })
        re_run_resp = MagicMock()
        re_run_resp.content = re_run_json

        mock_request = MagicMock()
        mock_request.question = QUESTION
        mock_request.workspace_id = "ws-1"
        mock_request.repo_path = str(tmp_path)

        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(return_value=re_run_resp)

        with patch(
            "companybrain.api.routes.query.ExplorationAgent"
        ) as MockAgent:
            MockAgent.return_value.explore = AsyncMock(return_value=exploration_result)
            result = await _run_exploration_agent(
                request=mock_request,
                initial_response=low_conf_response,
                assembled_context="sparse context",
                user_content="QUESTION: " + QUESTION,
                intent="concept",
                provider=mock_provider,
            )

        assert result.telemetry.get("exploration_agent_invoked") is True
        assert result.telemetry.get("exploration_rounds") == 1
        assert result.telemetry.get("exploration_tool_calls") == 2
        assert result.confidence.level == "high"

    @pytest.mark.asyncio
    async def test_telemetry_set_on_exploration_error(self, tmp_path):
        from companybrain.api.routes.query import _run_exploration_agent

        low_conf_response = QueryResponse(
            summary="I don't know.",
            confidence=Confidence(level="low", rationale="nothing found"),
        )

        mock_request = MagicMock()
        mock_request.question = QUESTION
        mock_request.workspace_id = "ws-1"
        mock_request.repo_path = str(tmp_path)

        with patch(
            "companybrain.api.routes.query.ExplorationAgent"
        ) as MockAgent:
            MockAgent.return_value.explore = AsyncMock(side_effect=RuntimeError("boom"))
            result = await _run_exploration_agent(
                request=mock_request,
                initial_response=low_conf_response,
                assembled_context=None,
                user_content="QUESTION: " + QUESTION,
                intent="concept",
                provider=MagicMock(),
            )

        # Falls back to initial response with error recorded
        assert result.summary == "I don't know."
        assert result.telemetry.get("exploration_agent_invoked") is False
        assert "boom" in result.telemetry.get("exploration_error", "")
