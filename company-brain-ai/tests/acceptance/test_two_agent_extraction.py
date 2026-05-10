"""Acceptance tests for ADR-0048 two-agent batched extraction.

These tests verify the contract from ADR-0048 §"Action Items" #7:
  - SpecialistAgent parses and returns a plan with the expected shape.
  - ContextAgent processes 8-method batches and returns 8 results.
  - Total LLM calls from SpecialistAgent (1) + ContextAgent (≤8) is < 15.

All LLM calls are mocked so these tests run without a live API key.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from companybrain.agents.context_agent import ContextAgent
from companybrain.agents.specialist_agent import SpecialistAgent


# ── Shared fixtures ───────────────────────────────────────────────────────────

@dataclass
class MockChunk:
    file_path: str
    qname: str
    kind: Literal["method"] = "method"
    body: str = "public List<Payer> getPayerCompetitors(String lobId) { return repo.find(lobId); }"
    header_context: str = "class CompetitivenessPlanRepository { @Autowired private Db db; }"
    import_context: str = "import java.util.List;\nimport org.springframework.stereotype.Repository;"
    body_hash: str = "deadbeef"
    language: str = "java"
    sibling_signatures: list = None

    def __post_init__(self):
        if self.sibling_signatures is None:
            self.sibling_signatures = []


def _specialist_plan_json() -> str:
    return json.dumps({
        "plan": [
            {
                "file": "src/CompetitivenessController.java",
                "role": "controller",
                "methods": ["getPayerCompetitors"],
                "relevance": 1.0,
                "reason": "entry handler for POST /competitiveness/summary/competitors/payer",
            },
            {
                "file": "src/CompetitivenessPlanRepository.java",
                "role": "repository",
                "methods": ["getPayerCompetitors", "getPayerPlans", "getMetrics",
                            "countByLob", "findByPayer", "upsertPlan",
                            "deletePayer", "bulkInsert"],
                "relevance": 1.0,
                "reason": "called by service.getPayerCompetitors via planRepo delegation",
            },
        ],
        "skip_dto": ["NiqAPIRequest", "PayerCompetitorDTO", "Filters"],
    })


def _context_results_json(qnames: list[str]) -> str:
    return json.dumps({
        "results": [
            {
                "qname": q,
                "entity": {
                    "entity_type": "Function",
                    "name": q,
                    "signature": f"public List<?> {q.split('.')[-1]}(String id)",
                    "confidence": 0.9,
                    "query_text": "SELECT c.lob FROM competitiveness c WHERE c.payer_id = :id",
                    "code_snippet": "return repo.find(id);",
                },
                "edges": [
                    {"edge_type": "READS_COLUMN", "target": "competitiveness.lob",
                     "confidence": 0.9, "evidence": "SQL fragment"},
                    {"edge_type": "CALLS", "target": "Db.find",
                     "confidence": 0.8, "evidence": "repo.find(lobId)"},
                ],
                "business_context": {
                    "purpose": f"Retrieves payer competitor data for {q}",
                    "change_risk": "HIGH",
                    "data_sensitivity": "PII",
                    "invariants": ["lobId must not be null"],
                    "side_effects": [],
                    "failure_modes": ["empty result when lob not found"],
                    "owner_team": "competitiveness-team",
                },
            }
            for q in qnames
        ]
    })


# ── Tests ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_specialist_agent_returns_plan_with_repository(tmp_path):
    """SpecialistAgent must include CompetitivenessPlanRepository in its plan."""
    handler_file = tmp_path / "CompetitivenessController.java"
    handler_file.write_text(
        "@RestController\n"
        "@PostMapping('/competitiveness/summary/competitors/payer')\n"
        "public class CompetitivenessController {\n"
        "  public ResponseEntity<?> getPayerCompetitors(@RequestBody NiqAPIRequest req) {\n"
        "    return service.getPayerCompetitors(req.getLobId());\n"
        "  }\n"
        "}\n"
    )

    mock_provider = MagicMock()
    mock_provider.chat_json = AsyncMock(return_value=_specialist_plan_json())

    with patch("companybrain.agents.specialist_agent.get_provider", return_value=mock_provider):
        agent = SpecialistAgent()
        plan = await agent.plan(
            endpoint="/competitiveness/summary/competitors/payer",
            http_method="POST",
            entry_handler_path=str(handler_file),
            candidate_files=[
                ("src/CompetitivenessController.java", "controller", 5),
                ("src/DefaultCompetitivenessService.java", "service", 12),
                ("src/CompetitivenessPlanRepository.java", "repository", 34),
            ],
        )

    file_names = {p["file"] for p in plan.plan}
    assert any("CompetitivenessPlanRepository" in f for f in file_names), (
        f"CompetitivenessPlanRepository not in plan files: {file_names}"
    )
    assert "NiqAPIRequest" in plan.skip_dto


@pytest.mark.asyncio
async def test_context_agent_8_methods_returns_8_results():
    """8 method chunks in one batch must return 8 results, all with entities."""
    qnames = [f"CompetitivenessPlanRepository.method{i}" for i in range(8)]
    chunks = [
        MockChunk(
            file_path="src/CompetitivenessPlanRepository.java",
            qname=q,
            body=f"public List<?> method{i}(String id) {{ return db.find(id); }}",
        )
        for i, q in enumerate(qnames)
    ]

    mock_provider = MagicMock()
    mock_provider.chat_json = AsyncMock(return_value=_context_results_json(qnames))

    with patch("companybrain.agents.context_agent.get_provider", return_value=mock_provider):
        agent = ContextAgent()
        results = await agent.extract_batch(chunks)

    assert len(results) == 8, f"Expected 8 results, got {len(results)}"
    assert all(r.entity is not None for r in results), "All results must have entities"

    # At least one result should have a non-empty query_text (SQL extraction quality)
    query_texts = [r.entity.query_text for r in results if r.entity]
    assert any(qt for qt in query_texts), "At least one entity must have query_text"


@pytest.mark.asyncio
async def test_total_llm_calls_under_15_for_typical_endpoint(tmp_path):
    """SpecialistAgent (1 call) + ContextAgent batches (≤8) = total < 15."""
    handler_file = tmp_path / "CompetitivenessController.java"
    handler_file.write_text("class CompetitivenessController {}")

    specialist_call_count = 0
    context_call_count = 0

    async def mock_specialist_chat_json(*args, **kwargs):
        nonlocal specialist_call_count
        specialist_call_count += 1
        return _specialist_plan_json()

    async def mock_context_chat_json(*args, **kwargs):
        nonlocal context_call_count
        context_call_count += 1
        # Return results for whatever batch was sent
        import re
        user_msg = kwargs.get("messages", args[0] if args else [])
        if isinstance(user_msg, list):
            qnames_found = [
                m.group(1)
                for msg in user_msg
                if hasattr(msg, "content")
                for m in re.finditer(r'qname="([^"]+)"', msg.content)
            ]
        else:
            qnames_found = []
        if not qnames_found:
            qnames_found = ["Foo.method0"]
        return _context_results_json(qnames_found)

    specialist_provider = MagicMock()
    specialist_provider.chat_json = mock_specialist_chat_json

    context_provider = MagicMock()
    context_provider.chat_json = mock_context_chat_json

    with patch("companybrain.agents.specialist_agent.get_provider", return_value=specialist_provider):
        plan = await SpecialistAgent().plan(
            endpoint="/competitiveness/summary/competitors/payer",
            http_method="POST",
            entry_handler_path=str(handler_file),
            candidate_files=[("src/Repo.java", "repository", 30)],
        )

    # Simulate ContextAgent batches: 8 methods in plan → 1 batch of 8 → 1 call
    all_methods = [
        MockChunk(
            file_path="src/CompetitivenessPlanRepository.java",
            qname=f"CompetitivenessPlanRepository.{m}",
        )
        for entry in plan.plan
        for m in entry.get("methods", [])
    ]

    # Process in batches of 8
    batch_size = 8
    with patch("companybrain.agents.context_agent.get_provider", return_value=context_provider):
        agent = ContextAgent()
        for i in range(0, max(len(all_methods), 1), batch_size):
            batch = all_methods[i:i + batch_size]
            if batch:
                await agent.extract_batch(batch)

    total_llm_calls = specialist_call_count + context_call_count
    assert total_llm_calls < 15, (
        f"Total LLM calls {total_llm_calls} must be < 15. "
        f"Specialist: {specialist_call_count}, Context: {context_call_count}"
    )


@pytest.mark.asyncio
async def test_specialist_agent_skip_dto_list_populated():
    """skip_dto list must be non-empty and contain expected DTO names."""
    handler_file = Path("/tmp/Handler.java")  # will fail read but graceful
    mock_provider = MagicMock()
    mock_provider.chat_json = AsyncMock(return_value=_specialist_plan_json())

    with patch("companybrain.agents.specialist_agent.get_provider", return_value=mock_provider):
        agent = SpecialistAgent()
        plan = await agent.plan(
            endpoint="/competitiveness/summary/competitors/payer",
            http_method="POST",
            entry_handler_path=str(handler_file),
            candidate_files=[],
        )

    assert len(plan.skip_dto) >= 1, "skip_dto must not be empty"
    assert "NiqAPIRequest" in plan.skip_dto
