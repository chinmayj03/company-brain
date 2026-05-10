"""Unit tests for SpecialistAgent (ADR-0048)."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from companybrain.agents.specialist_agent import ExtractionPlan, SpecialistAgent


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_plan_json(plan_entries=None, skip_dto=None) -> str:
    return json.dumps({
        "plan": plan_entries or [
            {
                "file": "src/CompetitivenessController.java",
                "role": "controller",
                "methods": ["getPayer"],
                "relevance": 1.0,
                "reason": "entry handler",
            }
        ],
        "skip_dto": skip_dto or ["PayerDTO", "NiqRequest"],
    })


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestSpecialistAgentParse:
    def test_parse_valid_json(self):
        agent = SpecialistAgent.__new__(SpecialistAgent)
        raw = _make_plan_json()
        result = agent._parse(raw)
        assert isinstance(result, ExtractionPlan)
        assert len(result.plan) == 1
        assert result.plan[0]["role"] == "controller"
        assert "PayerDTO" in result.skip_dto

    def test_parse_markdown_fenced_json(self):
        agent = SpecialistAgent.__new__(SpecialistAgent)
        raw = "```json\n" + _make_plan_json() + "\n```"
        result = agent._parse(raw)
        assert len(result.plan) == 1

    def test_parse_empty_response_returns_empty_plan(self):
        agent = SpecialistAgent.__new__(SpecialistAgent)
        result = agent._parse("")
        assert result.plan == []
        assert result.skip_dto == []

    def test_parse_malformed_json_returns_empty_plan(self):
        agent = SpecialistAgent.__new__(SpecialistAgent)
        result = agent._parse("not json at all")
        assert result.plan == []

    def test_parse_skips_non_dict_plan_entries(self):
        agent = SpecialistAgent.__new__(SpecialistAgent)
        raw = json.dumps({"plan": ["bad", 42, {"file": "ok.java", "role": "service", "methods": [], "relevance": 0.8, "reason": "x"}], "skip_dto": []})
        result = agent._parse(raw)
        # Only dict entries are kept
        assert len(result.plan) == 1
        assert result.plan[0]["file"] == "ok.java"


class TestSpecialistAgentBuildPrompt:
    def test_manifest_table_format(self):
        agent = SpecialistAgent.__new__(SpecialistAgent)
        files = [("src/Foo.java", "service", 12), ("src/Bar.java", "repository", 34)]
        md = agent._build_manifest_table(files)
        assert "| file | role | size_kb |" in md
        assert "| src/Foo.java | service | 12 |" in md
        assert "| src/Bar.java | repository | 34 |" in md

    def test_user_prompt_includes_endpoint_and_handler(self):
        agent = SpecialistAgent.__new__(SpecialistAgent)
        prompt = agent._build_user_prompt(
            "/api/test", "POST", "handler.java", "class Handler {}", "| file | role | size_kb |\n|---|---|---|"
        )
        assert "POST /api/test" in prompt
        assert "handler.java" in prompt
        assert "class Handler {}" in prompt


@pytest.mark.asyncio
class TestSpecialistAgentPlanCall:
    async def test_plan_calls_provider_and_returns_plan(self, tmp_path):
        handler_file = tmp_path / "CompetitivenessController.java"
        handler_file.write_text("class CompetitivenessController { public void get() {} }")

        mock_provider = MagicMock()
        mock_provider.chat_json = AsyncMock(return_value=_make_plan_json())

        with patch("companybrain.agents.specialist_agent.get_provider", return_value=mock_provider):
            agent = SpecialistAgent()
            plan = await agent.plan(
                endpoint="/api/competitiveness",
                http_method="POST",
                entry_handler_path=str(handler_file),
                candidate_files=[("src/Foo.java", "service", 10)],
            )

        assert len(plan.plan) == 1
        assert plan.plan[0]["role"] == "controller"
        assert "PayerDTO" in plan.skip_dto
        mock_provider.chat_json.assert_called_once()

    async def test_plan_returns_empty_when_llm_fails(self, tmp_path):
        handler_file = tmp_path / "Handler.java"
        handler_file.write_text("")

        mock_provider = MagicMock()
        mock_provider.chat_json = AsyncMock(side_effect=RuntimeError("LLM timeout"))

        with patch("companybrain.agents.specialist_agent.get_provider", return_value=mock_provider):
            agent = SpecialistAgent()
            plan = await agent.plan(
                endpoint="/api/foo",
                http_method="GET",
                entry_handler_path=str(handler_file),
                candidate_files=[],
            )

        assert plan.plan == []
        assert plan.skip_dto == []
