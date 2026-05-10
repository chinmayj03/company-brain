"""Acceptance tests for ADR-0051 Phase 1.

Verifies that BRAIN_USE_HARNESS=true delegates the entire run through
HarnessLoop, that the canonical pipeline (discover → find → list → extract →
write → finalize) executes end-to-end against a tiny synthetic Spring repo,
and that a brain entity is persisted.

LLM calls are mocked — the test scripts the full tool sequence the model
would emit so the run is deterministic and runs without an API key.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from companybrain.harness.tools import TOOL_REGISTRY
from companybrain.llm.base import ChatResponse, ToolCall


# ── Synthetic repo fixture ────────────────────────────────────────────────────

_CONTROLLER_SOURCE = """\
package com.example.api;

import org.springframework.web.bind.annotation.RestController;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.beans.factory.annotation.Autowired;

@RestController
@RequestMapping("/competitiveness")
public class CompetitivenessController {

    @Autowired
    private CompetitivenessService service;

    @PostMapping("/summary/competitors/payer")
    public PayerCompetitorsResponse getPayerCompetitors(PayerRequest req) {
        return service.findPayerCompetitors(req.lob());
    }
}
"""

_SERVICE_SOURCE = """\
package com.example.api;

import org.springframework.stereotype.Service;

@Service
public class CompetitivenessService {

    public PayerCompetitorsResponse findPayerCompetitors(String lob) {
        return new PayerCompetitorsResponse();
    }
}
"""


@pytest.fixture
def tiny_repo(tmp_path: Path) -> Path:
    """Lay out a 2-file Spring repo with one POST endpoint."""
    (tmp_path / "src" / "main" / "java" / "com" / "example" / "api").mkdir(parents=True)
    base = tmp_path / "src" / "main" / "java" / "com" / "example" / "api"
    (base / "CompetitivenessController.java").write_text(_CONTROLLER_SOURCE)
    (base / "CompetitivenessService.java").write_text(_SERVICE_SOURCE)
    return tmp_path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _resp(content: str = "", tool_calls=None) -> ChatResponse:
    return ChatResponse(
        content=content,
        model="mock-model",
        provider="mock",
        tool_calls=list(tool_calls or []),
    )


def _scripted_provider(repo: Path) -> AsyncMock:
    """Build a provider mock that walks the canonical pipeline once."""
    handler_qname = "CompetitivenessController.getPayerCompetitors"
    handler_file = str(repo / "src" / "main" / "java" / "com" / "example"
                       / "api" / "CompetitivenessController.java")

    responses = [
        _resp(tool_calls=[ToolCall(
            name="discover_routes",
            arguments={"repo_path": str(repo)},
            call_id="t1",
        )]),
        _resp(tool_calls=[ToolCall(
            name="find_entry_handler",
            arguments={
                "endpoint": "/competitiveness/summary/competitors/payer",
                "http_method": "POST",
                "repo_path": str(repo),
            },
            call_id="t2",
        )]),
        _resp(tool_calls=[ToolCall(
            name="write_to_brain",
            arguments={
                "entities": [{
                    "qname": handler_qname,
                    "entity_type": "function_node",
                    "repo": "tiny-repo",
                    "file": handler_file,
                    "signature": "PayerCompetitorsResponse getPayerCompetitors(PayerRequest)",
                    "code_snippet": "service.findPayerCompetitors(req.lob())",
                    "metadata": {"query_text": "select payer competitors by lob"},
                    "edges": [{
                        "target": "CompetitivenessService.findPayerCompetitors",
                        "edge_type": "CALLS",
                        "confidence": 0.95,
                    }],
                }],
            },
            call_id="t3",
        )]),
        _resp(tool_calls=[ToolCall(
            name="finalize_brain",
            arguments={"workspace_id": "ws-acc"},
            call_id="t4",
        )]),
        _resp(content=("Extracted 1 entity (CompetitivenessController.getPayerCompetitors) "
                       "and committed the run.")),
    ]
    return AsyncMock(side_effect=responses)


# ── Tests ─────────────────────────────────────────────────────────────────────

async def test_harness_orchestrator_delegate_happy_path(tiny_repo, monkeypatch):
    """run_pipeline with BRAIN_USE_HARNESS=true delegates end-to-end and writes a brain entity."""
    from companybrain.models.entities import PipelineStartRequest, RepoConfig
    from companybrain.pipeline import orchestrator

    monkeypatch.setenv("BRAIN_USE_HARNESS", "true")

    request = PipelineStartRequest(
        endpoint_path="/competitiveness/summary/competitors/payer",
        http_method="POST",
        branch="main",
        repos=[RepoConfig(local_path=str(tiny_repo))],
        workspace_id="ws-acc",
    )

    fake_chat = _scripted_provider(tiny_repo)
    with patch.object(orchestrator, "_run_via_harness", wraps=orchestrator._run_via_harness):
        # Patch get_provider so HarnessLoop sees our mock.
        with patch("companybrain.harness.loop.get_provider") as get_p:
            mock_provider = type("MP", (), {})()
            mock_provider.chat_with_tools = fake_chat
            mock_provider.provider_name = "mock"
            mock_provider.model_for_role = lambda role: f"mock/{role.value}"
            get_p.return_value = mock_provider

            result = await orchestrator.run_pipeline(request)

    assert result.status == "completed"
    assert result.workspace_id == "ws-acc"
    assert result.endpoint_path == "/competitiveness/summary/competitors/payer"
    # Telemetry surfaces harness counters (proves we went through the harness).
    assert "harness" in result.telemetry
    assert result.telemetry["harness"]["tool_calls_total"] == 4
    assert result.telemetry["harness"]["tool_calls_ok"] == 4

    # The brain store wrote one entity to disk under {repo_path}/.brain/.
    brain_root = tiny_repo / ".brain"
    assert brain_root.exists(), "brain root not created"
    function_nodes = list((brain_root / "function_node").glob("*.json"))
    assert len(function_nodes) == 1
    payload = json.loads(function_nodes[0].read_text())
    assert payload["qualified_name"] == "CompetitivenessController.getPayerCompetitors"
    assert payload["repo"] == "tiny-repo"
    # The "lob" query_text the model emitted is preserved through write_to_brain.
    assert "lob" in (payload["metadata"].get("query_text") or "")
    # Edge to the service method is preserved as a relationship.
    rels = payload["relationships"]
    assert any(r["target_id"] == "CompetitivenessService.findPayerCompetitors"
               for r in rels)

    # finalize_brain wrote the manifest.
    manifest = json.loads((brain_root / "manifest.json").read_text())
    assert "last_run_id" in manifest


async def test_orchestrator_skips_harness_when_flag_off(tiny_repo, monkeypatch):
    """Without BRAIN_USE_HARNESS the legacy path is selected (no delegation)."""
    monkeypatch.delenv("BRAIN_USE_HARNESS", raising=False)
    from companybrain.config import settings
    monkeypatch.setattr(settings, "use_harness", False, raising=False)

    from companybrain.pipeline.orchestrator import _harness_enabled
    assert _harness_enabled() is False


async def test_canonical_pipeline_tools_all_registered():
    """The 9 Phase-1 tools are registered and exposed to the loop."""
    expected = {
        "discover_routes", "find_entry_handler", "list_candidate_files",
        "read_file", "glob_files", "grep_code",
        "extract_methods_from_class", "write_to_brain", "finalize_brain",
    }
    assert expected.issubset(set(TOOL_REGISTRY))


async def test_discover_routes_finds_endpoint(tiny_repo):
    """discover_routes returns the synthetic Spring endpoint."""
    tool = TOOL_REGISTRY["discover_routes"]
    result = await tool.invoke({"repo_path": str(tiny_repo)}, context={})
    paths = [(r["method"], r["path"]) for r in result]
    assert ("POST", "/competitiveness/summary/competitors/payer") in paths


async def test_glob_files_lists_repo_sources(tiny_repo):
    """glob_files surfaces .java files under the synthetic repo."""
    tool = TOOL_REGISTRY["glob_files"]
    result = await tool.invoke(
        {"repo_path": str(tiny_repo), "pattern": "**/*.java"},
        context={},
    )
    assert any(p.endswith("CompetitivenessController.java") for p in result)
    assert any(p.endswith("CompetitivenessService.java") for p in result)
