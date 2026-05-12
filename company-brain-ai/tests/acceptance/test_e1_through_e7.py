"""ADR-0061 acceptance — E1 through E7 contract tests.

These tests stub external services (LLM provider, Neo4j, Qdrant) so they run
in CI without infrastructure. Each test asserts the *observable contract* the
ADR commits to:

  E1 — exploration_agent fires on hard queries and records telemetry.
  E2 — re-read re-fetches source for shaky citations and re-runs the LLM.
  E3 — trace_exception returns the documented tree shape.
  E4 — diff_since respects ``date`` and returns sorted-by-recency entities.
  E5 — clarification returns >= 2 interpretations for ambiguous queries.
  E6 — cross_repo_similarity emits SimilarTo edges when a near-duplicate is found.
  E7 — DiagramExtractor produces a Diagram entity with components.

LLM-backed bits use a deterministic fake provider so JSON parsing and the
telemetry contract are exercised without hitting Anthropic.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional
from unittest.mock import AsyncMock, patch

import pytest

from companybrain.llm import ChatMessage
from companybrain.llm.base import ToolCall


# ── Fake LLM provider (mirrors ADR-0055 / ADR-0059 patterns) ──────────────────

@dataclass
class _PlannedReply:
    """One response we will hand back to the agent loop.

    tool_calls is the list of ToolCall objects to return; if empty the reply
    is treated as the agent's final answer (content goes back).
    """
    content: str
    tool_calls: list[ToolCall]


class _FakeProvider:
    def __init__(self, scripted: list[_PlannedReply], chat_text: str = ""):
        self._scripted = scripted
        self._chat_text = chat_text
        self.chat_calls: list[list[ChatMessage]] = []
        self.tool_calls: list[list[ChatMessage]] = []
        self.provider_name = "fake"

    def model_for_role(self, role) -> str:
        return "fake-model"

    async def chat(self, messages, role=None, max_tokens=2048, temperature=0.1):
        self.chat_calls.append(list(messages))
        from companybrain.llm.base import ChatResponse
        return ChatResponse(content=self._chat_text, model="fake-model",
                            provider="fake", input_tokens=0, output_tokens=0)

    async def chat_json(self, messages, role=None, max_tokens=2048):
        self.chat_calls.append(list(messages))
        return self._chat_text

    async def chat_with_tools(self, messages, tools, role=None, max_tokens=2048):
        self.tool_calls.append(list(messages))
        from companybrain.llm.base import ChatResponse
        if not self._scripted:
            return ChatResponse(content="(no more replies)", model="fake",
                                provider="fake", input_tokens=0, output_tokens=0,
                                tool_calls=[])
        reply = self._scripted.pop(0)
        return ChatResponse(
            content=reply.content, model="fake", provider="fake",
            input_tokens=0, output_tokens=0,
            tool_calls=reply.tool_calls,
        )


# ── E1: ExplorationAgent fires + records telemetry ───────────────────────────

@pytest.mark.asyncio
async def test_e1_exploration_agent_fires_on_hard_query(tmp_path: Path):
    """ExplorationAgent must run when initial confidence is low.

    Set up a tiny repo with one file containing the literal "lob" four times,
    then drive the agent through a grep → final-answer sequence.
    """
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "controller.py").write_text(
        "def get_lob():\n"
        "    if value == 'lob':\n"
        "        return 'lob'\n"
        "    return 'lob'\n"
    )
    scripted = [
        _PlannedReply(
            content="thinking — let me grep",
            tool_calls=[ToolCall(
                name="grep_code", call_id="t1",
                arguments={"pattern": "lob", "glob": "*.py"},
            )],
        ),
        _PlannedReply(
            content=(
                "Found 4 references to the literal 'lob' in "
                "src/controller.py at lines 1, 2, 3, 4."
            ),
            tool_calls=[],
        ),
    ]
    provider = _FakeProvider(scripted)

    from companybrain.agents import exploration_agent as ea
    with patch.object(ea, "get_provider", return_value=provider):
        agent = ea.ExplorationAgent(workspace_id="dev", repo_path=str(tmp_path))
        result = await agent.run(
            "which 4 places in the codebase use the literal 'lob'?"
        )
    assert result.steps >= 1
    assert "src/controller.py" in result.text
    assert len(result.tool_calls) >= 1
    assert result.tool_calls[0]["tool"] == "grep_code"
    assert result.capped is False


# ── E2: re-read re-fetches source when confidence is low ─────────────────────

@pytest.mark.asyncio
async def test_e2_reread_runs_llm_when_citation_low_confidence(tmp_path: Path):
    """When a citation is below 0.7, re-read fires an additional LLM call."""
    from companybrain.api.routes import query_reread as qr
    from companybrain.models.query_response import Citation, Confidence, QueryResponse

    # Set up a fake .brain/ so the URN can be resolved to a file. JsonFileBrainStore
    # validates the loaded dict against BrainEntity's dataclass fields, so we
    # include the required `repo` field.
    brain = tmp_path / ".brain"
    (brain / "component").mkdir(parents=True)
    (brain / "component" / "Foo.json").write_text(json.dumps({
        "id": "urn:cb:dev:src:demo:component:Foo",
        "entity_type": "component",
        "repo": "demo",
        "file": "src/Foo.py",
        "qualified_name": "Foo",
    }))
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "Foo.py").write_text("class Foo:\n    pass\n")

    prior = QueryResponse(
        summary="prior draft mentions Foo with low confidence",
        confidence=Confidence(level="low", rationale="zone was sparse"),
        affected_entities=[Citation(
            urn="urn:cb:dev:src:demo:component:Foo",
            name="Foo", why_relevant="possibly used", confidence=0.4,
        )],
    )
    provider = _FakeProvider(scripted=[], chat_text=json.dumps({
        "summary": "REREAD: Foo is a placeholder class.",
        "confidence": {"level": "high", "rationale": "verified from source"},
    }))
    with patch.object(qr, "get_provider", return_value=provider):
        out = await qr.maybe_reread(
            question="what is Foo?", response=prior,
            workspace_id="00000000-0000-0000-0000-000000000001",
            repo_path=str(tmp_path),
        )
    assert out.telemetry.get("reread_invoked") is True
    assert "REREAD" in out.summary or "placeholder" in out.summary
    # The provider was actually called once for the re-read.
    assert len(provider.chat_calls) == 1


@pytest.mark.asyncio
async def test_e2_reread_skips_when_no_source(tmp_path: Path):
    """If we can't resolve the file, we leave the response alone."""
    from companybrain.api.routes import query_reread as qr
    from companybrain.models.query_response import Citation, Confidence, QueryResponse

    prior = QueryResponse(
        summary="prior",
        confidence=Confidence(level="low", rationale="t"),
        affected_entities=[Citation(
            urn="urn:cb:nope", name="N", why_relevant="x", confidence=0.3,
        )],
    )
    out = await qr.maybe_reread(
        question="?", response=prior,
        workspace_id="ws", repo_path=str(tmp_path),
    )
    assert out.telemetry.get("reread_invoked") is False
    assert out.summary == "prior"


# ── E3: trace_exception walks THROWS / CATCHES ───────────────────────────────

@pytest.mark.asyncio
async def test_e3_trace_exception_returns_tree_shape():
    """The Neo4j queries are mocked; we only assert the return shape."""
    from companybrain.mcp.tools import trace_exception as te

    class _FakeSession:
        def __init__(self):
            self.queries: list[tuple[str, dict]] = []

        async def run(self, q, **kwargs):
            self.queries.append((q, kwargs))
            # Inject canned data for each call.
            class _R:
                def __init__(self, payload):
                    self._payload = payload

                async def single(self):
                    return self._payload[0] if self._payload else None

                async def data(self):
                    return self._payload
            if "ENDS WITH" in q or "RETURN n.id" in q:
                return _R([{"id": "urn:cb:exc:1",
                            "name": "DatabaseOperationException"}])
            if "[r:THROWS]" in q:
                return _R([{"id": "urn:cb:m:1",
                            "name": "JpaQueryExecutor.execute",
                            "file": "src/Executor.java"}])
            if "[r:CATCHES]" in q:
                return _R([{"id": "urn:cb:m:2",
                            "name": "GlobalExceptionHandler.handle",
                            "file": "src/Handler.java"}])
            if "[r:WRAPS_EXCEPTION]" in q:
                return _R([])
            if "THROWS" in q and "CATCHES" in q:
                return _R([{"thrower_id": "urn:cb:m:1",
                            "thrower": "JpaQueryExecutor.execute",
                            "uncovered_callers": 2}])
            return _R([])

    class _FakeDriver:
        def __init__(self):
            self.session_obj = _FakeSession()

        def session(self):
            class _Ctx:
                def __init__(self, s): self._s = s
                async def __aenter__(self): return self._s
                async def __aexit__(self, *a): return False
            return _Ctx(self.session_obj)

        async def close(self):
            pass

    fake_driver = _FakeDriver()
    with patch.object(te, "_driver", return_value=fake_driver):
        tree = await te.trace_exception("DatabaseOperationException")
    assert tree["resolved"] is True
    assert tree["thrown_by"]
    assert tree["thrown_by"][0]["name"] == "JpaQueryExecutor.execute"
    assert tree["caught_by"]
    assert "GlobalExceptionHandler.handle" in tree["caught_by"][0]["name"]
    assert tree["unhandled_at"]


# ── E4: diff_since respects date + sorts by recency ─────────────────────────

@pytest.mark.asyncio
async def test_e4_diff_since_returns_recent_changes(tmp_path: Path, monkeypatch):
    """We mock subprocess.run so we don't depend on a real git repo in CI."""
    from companybrain.mcp.tools import diff_since as ds

    # Seed a fake .brain/ index.
    brain = tmp_path / ".brain"
    (brain / "component").mkdir(parents=True)
    (brain / "component" / "A.json").write_text(json.dumps({
        "id": "urn:cb:A", "qualified_name": "A", "file": "src/A.py",
    }))
    (brain / "component" / "B.json").write_text(json.dumps({
        "id": "urn:cb:B", "qualified_name": "B", "file": "src/B.py",
    }))

    class _FakeCompleted:
        def __init__(self, stdout):
            self.stdout = stdout
            self.returncode = 0

    fake_outputs = iter([
        # git log --since= --name-only
        _FakeCompleted("src/A.py\nsrc/B.py\n"),
        # last commit for src/A.py
        _FakeCompleted("abc123|alice|2026-05-01T00:00:00+00:00|tweak A"),
        # last commit for src/B.py
        _FakeCompleted("def456|bob|2026-04-20T00:00:00+00:00|tweak B"),
    ])

    def _fake_run(cmd, **kw):
        return next(fake_outputs)

    monkeypatch.setattr(ds.subprocess, "run", _fake_run)

    rows = await ds.diff_since(date="2026-04-01", repo=str(tmp_path))
    assert len(rows) == 2
    assert all(r["last_touched_at"] >= "2026-04-01" for r in rows)
    # Sorted recent-first.
    assert rows[0]["last_touched_at"] >= rows[1]["last_touched_at"]


# ── E5: clarification returned for ambiguous queries ────────────────────────

def test_e5_clarification_returned_for_ambiguous_query(tmp_path: Path):
    """The query route surfaces interpretations before calling the LLM."""
    from companybrain.api.routes.clarification import detect_ambiguity

    brain = tmp_path / ".brain"
    (brain / "DatabaseColumn").mkdir(parents=True)
    (brain / "DatabaseColumn" / "lob.json").write_text(json.dumps({
        "qualified_name": "plan_info.lob", "name": "lob",
    }))
    (brain / "OpenAPISchema").mkdir(parents=True)
    (brain / "OpenAPISchema" / "PlanRequest.json").write_text(json.dumps({
        "qualified_name": "PlanRequest.lob", "name": "lob",
    }))

    out = detect_ambiguity("rename the lob column", repo_path=str(tmp_path))
    assert out.ambiguous is True
    assert len(out.interpretations or []) >= 2
    assert any(opt["id"] == "both" for opt in (out.interpretations or []))


# ── E6: cross_repo_similarity surfaces SimilarTo ────────────────────────────

def test_e6_cross_repo_similarity_surfaces(monkeypatch):
    """Seed two fake workspaces in Qdrant; insight must reference the other."""
    from companybrain.retrieval import cross_repo_similarity as crs

    class _FakeQClient:
        def get_collections(self):
            class _C:
                collections = [
                    type("c", (), {"name": "brain__dev__code"})(),
                    type("c", (), {"name": "brain__acme-billing__code"})(),
                ]
            return _C()

        def search(self, collection_name, query_vector, limit, with_payload):
            assert collection_name == "brain__acme-billing__code"
            return [type("Hit", (), {
                "id": "uuid-1", "score": 0.92,
                "payload": {
                    "urn": "urn:acme:m:findActiveCustomers",
                    "qualified_name": "BillingRepo.findActiveCustomers",
                },
            })()]

    class _FakeEmbedder:
        def embed(self, text):
            return [0.0] * 16

    monkeypatch.setattr(crs, "make_client", lambda: _FakeQClient())
    monkeypatch.setattr(crs, "make_embedder", lambda: _FakeEmbedder())

    insights = crs.find_similar(
        own_workspace_slug="dev",
        seeds=[{"urn": "urn:dev:m:getPayerCompetitors",
                "name": "getPayerCompetitors",
                "text": "fetch active payer competitors"}],
    )
    assert len(insights) == 1
    assert insights[0].target_workspace == "acme-billing"
    assert insights[0].target_name == "BillingRepo.findActiveCustomers"
    assert insights[0].score >= crs.MIN_SCORE


def test_e6_cross_repo_skips_self_workspace(monkeypatch):
    """The caller's own workspace must not appear in results."""
    from companybrain.retrieval import cross_repo_similarity as crs

    class _OnlySelf:
        def get_collections(self):
            class _C:
                collections = [
                    type("c", (), {"name": "brain__dev__code"})(),
                ]
            return _C()

        def search(self, *a, **kw):
            raise AssertionError("should not query own workspace")

    monkeypatch.setattr(crs, "make_client", lambda: _OnlySelf())
    monkeypatch.setattr(crs, "make_embedder", lambda: type("E", (), {
        "embed": lambda self, t: [0.0] * 16,
    })())

    insights = crs.find_similar(
        own_workspace_slug="dev",
        seeds=[{"urn": "x", "name": "x", "text": "x"}],
    )
    assert insights == []


# ── E7: diagram extractor emits a Diagram entity ────────────────────────────

def test_e7_diagram_extracted_and_queryable(tmp_path: Path):
    """SVG fallback exercises the contract without a vision API key."""
    docs = tmp_path / "docs"
    docs.mkdir()
    svg = docs / "architecture.svg"
    svg.write_text(
        "<svg>"
        "<title>Architecture overview</title>"
        "<text>API</text><text>DB</text><text>Queue</text>"
        "</svg>"
    )
    from companybrain.extractors.diagram_extractor import DiagramExtractor
    de = DiagramExtractor()
    batch = de.extract(svg, svg.read_text(), repo="acme-svc")
    assert len(batch.diagrams) == 1
    d = batch.diagrams[0]
    assert d.repo == "acme-svc"
    assert d.title.lower().startswith("architecture")
    names = {c.name for c in d.components}
    assert {"API", "DB", "Queue"}.issubset(names)


def test_e7_diagram_extractor_registered_with_dispatch():
    """The extractor self-registers so universal-extraction picks it up."""
    from companybrain.extractors import dispatch
    from companybrain.extractors.diagram_extractor import DiagramExtractor
    assert any(isinstance(e, DiagramExtractor)
               for e in dispatch._SCHEMA_EXTRACTORS)
