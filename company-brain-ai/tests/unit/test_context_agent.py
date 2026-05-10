"""Unit tests for ContextAgent (ADR-0048)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from companybrain.agents.context_agent import ContextAgent, ContextAgentResult
from companybrain.pipeline.chunk_extractor import ExtractedChunkEntity, ExtractedEdge


# ── Helpers ────────────────────────────────────────────────────────────────────

@dataclass
class _MockChunk:
    file_path: str
    qname: str
    kind: Literal["method"] = "method"
    body: str = "public void method() {}"
    header_context: str = "class Foo { private int x; }"
    import_context: str = "import java.util.List;"
    body_hash: str = "abc123"
    language: str = "java"
    sibling_signatures: list = None

    def __post_init__(self):
        if self.sibling_signatures is None:
            self.sibling_signatures = []


def _make_chunk(qname: str, body: str = "public void method() {}") -> _MockChunk:
    return _MockChunk(
        file_path="src/Foo.java",
        qname=qname,
        body=body,
    )


def _make_results_json(qnames: list[str]) -> str:
    return json.dumps({
        "results": [
            {
                "qname": q,
                "entity": {
                    "entity_type": "Function",
                    "name": q,
                    "signature": f"public void {q.split('.')[-1]}()",
                    "confidence": 0.9,
                    "query_text": "",
                    "code_snippet": "// snippet",
                },
                "edges": [
                    {"edge_type": "CALLS", "target": "Other.method", "confidence": 0.8, "evidence": "line 5"}
                ],
                "business_context": {
                    "purpose": "test purpose",
                    "change_risk": "LOW",
                    "data_sensitivity": None,
                    "invariants": [],
                    "side_effects": [],
                    "failure_modes": [],
                    "owner_team": None,
                },
            }
            for q in qnames
        ]
    })


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestContextAgentParse:
    def test_parse_returns_one_result_per_chunk(self):
        agent = ContextAgent.__new__(ContextAgent)
        chunks = [_make_chunk("Foo.a"), _make_chunk("Foo.b")]
        raw = _make_results_json(["Foo.a", "Foo.b"])
        results = agent._parse(raw, chunks)
        assert len(results) == 2
        assert results[0].qname == "Foo.a"
        assert results[1].qname == "Foo.b"

    def test_parse_pads_with_empty_when_llm_returns_fewer(self):
        agent = ContextAgent.__new__(ContextAgent)
        chunks = [_make_chunk("Foo.a"), _make_chunk("Foo.b"), _make_chunk("Foo.c")]
        raw = _make_results_json(["Foo.a"])  # only 1 result for 3 chunks
        results = agent._parse(raw, chunks)
        assert len(results) == 3
        assert results[0].entity is not None
        assert results[1].entity is None  # padded
        assert results[2].entity is None  # padded

    def test_parse_empty_response_returns_empty_results(self):
        agent = ContextAgent.__new__(ContextAgent)
        chunks = [_make_chunk("Foo.a")]
        results = agent._parse("", chunks)
        assert len(results) == 1
        assert results[0].entity is None

    def test_parse_extracts_edges(self):
        agent = ContextAgent.__new__(ContextAgent)
        chunks = [_make_chunk("Foo.a")]
        raw = _make_results_json(["Foo.a"])
        results = agent._parse(raw, chunks)
        assert len(results[0].edges) == 1
        assert results[0].edges[0].edge_type == "CALLS"
        assert results[0].edges[0].target == "Other.method"

    def test_parse_extracts_business_context(self):
        agent = ContextAgent.__new__(ContextAgent)
        chunks = [_make_chunk("Foo.a")]
        raw = _make_results_json(["Foo.a"])
        results = agent._parse(raw, chunks)
        assert results[0].business_context.get("purpose") == "test purpose"
        assert results[0].business_context.get("change_risk") == "LOW"


class TestContextAgentBuildXml:
    def test_xml_includes_class_header_and_all_methods(self):
        agent = ContextAgent.__new__(ContextAgent)
        chunks = [_make_chunk("Foo.m1", "void m1(){}"), _make_chunk("Foo.m2", "void m2(){}")]
        xml = agent._build_user_xml(chunks)
        assert "<class_header" in xml
        assert '<method qname="Foo.m1"' in xml
        assert '<method qname="Foo.m2"' in xml
        assert "void m1(){}" in xml
        assert "void m2(){}" in xml

    def test_xml_includes_imports(self):
        agent = ContextAgent.__new__(ContextAgent)
        chunks = [_make_chunk("Foo.m")]
        xml = agent._build_user_xml(chunks)
        assert "<imports>" in xml
        assert "import java.util.List;" in xml


@pytest.mark.asyncio
class TestContextAgentExtractBatch:
    async def test_extract_batch_returns_result_per_chunk(self):
        chunks = [_make_chunk(f"Foo.method{i}") for i in range(3)]
        mock_provider = MagicMock()
        mock_provider.chat_json = AsyncMock(return_value=_make_results_json([c.qname for c in chunks]))

        with patch("companybrain.agents.context_agent.get_provider", return_value=mock_provider):
            agent = ContextAgent()
            results = await agent.extract_batch(chunks)

        assert len(results) == 3
        assert all(r.entity is not None for r in results)

    async def test_extract_batch_empty_input_returns_empty(self):
        mock_provider = MagicMock()
        mock_provider.chat_json = AsyncMock(return_value="")

        with patch("companybrain.agents.context_agent.get_provider", return_value=mock_provider):
            agent = ContextAgent()
            results = await agent.extract_batch([])

        assert results == []
        mock_provider.chat_json.assert_not_called()

    async def test_extract_batch_handles_llm_failure(self):
        chunks = [_make_chunk("Foo.m1"), _make_chunk("Foo.m2")]
        mock_provider = MagicMock()
        mock_provider.chat_json = AsyncMock(side_effect=RuntimeError("timeout"))

        with patch("companybrain.agents.context_agent.get_provider", return_value=mock_provider):
            agent = ContextAgent()
            results = await agent.extract_batch(chunks)

        assert len(results) == 2
        assert all(r.entity is None for r in results)

    async def test_extract_batch_8_chunks_returns_8_results(self):
        """ADR-0048 acceptance: 8 chunks in → 8 results out."""
        chunks = [_make_chunk(f"Foo.method{i}") for i in range(8)]
        mock_provider = MagicMock()
        mock_provider.chat_json = AsyncMock(return_value=_make_results_json([c.qname for c in chunks]))

        with patch("companybrain.agents.context_agent.get_provider", return_value=mock_provider):
            agent = ContextAgent()
            results = await agent.extract_batch(chunks)

        assert len(results) == 8
        assert all(r.entity is not None for r in results)
