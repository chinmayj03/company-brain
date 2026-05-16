# ADR-0068 — Native Agent Framework Integrations (langchain, crewai, autogen, openai_tools)

**Status:** Proposed
**Date:** 2026-05-11
**Inspired by:** ContextDB's `integrations/` module (Apache 2.0; pattern adopted, code is our own per LEGAL-CONTEXTDB-INTEGRATION.md)
**Sequenced with:** depends on ADR-0052 P5 (MCP server) being live; otherwise parallel-shippable. **The strategic linchpin for Product 4 (AI Agent Substrate) — this is what gets us into Cursor/Cognition/AI-vendor ecosystems.**

---

## Context

The brain exposes its capabilities through MCP today (per ADR-0052 P5, 10 tools at `companybrain/mcp/server.py`). MCP is the right long-term API. **But in 2026, most agent frameworks DON'T speak MCP yet.** They have their own memory / retrieval abstractions:

- **LangChain**: `BaseMemory`, `BaseRetriever`, `Tool`
- **CrewAI**: `Memory` interface, `Tool` decorator
- **AutoGen**: `Memory` (chat memory) + `Tool` registration
- **OpenAI Tools API** (raw OpenAI SDK): function calling specs

To reach AI-vendor platform teams (Persona 2 from PRODUCT-VISION) and individual agent builders (the developer market), we need to land **inside their existing surface area**. A `pip install companybrain-langchain` that drops into a langchain agent transparently is 100× lower friction than asking them to set up MCP.

ContextDB shipped this exact pattern — they have `integrations/{autogen,crewai,langchain,openai_tools}.py` modules. Their adoption curve was steep BECAUSE of those integrations: developers can keep their existing langchain/crewai code and just add ContextDB as a memory backend.

**Without this ADR**: every agent-vendor conversation requires us to convince them to adopt MCP first. Sales cycle stretches months longer.

**With this ADR**: a Cursor PM can `pip install companybrain-langchain`, drop us in, demo to their team in 10 minutes.

---

## Decision

A new sibling package `companybrain-integrations` that ships 4 framework adapters. Each adapter is a thin shim implementing the framework's native interface, talking to the brain's MCP server under the hood. **No backend changes** — pure add-on.

### Adapter shape (consistent across frameworks)

Each adapter exposes a `BrainMemory` (or framework-specific class name) that:

1. Implements the framework's memory / retriever interface
2. Talks to the brain's MCP server via stdio or HTTP
3. Accepts standard config: `workspace_id`, `mcp_url` (defaults to `http://localhost:8765`)
4. Adds framework-specific niceties (e.g., LangChain `BaseRetriever` returns LangChain `Document` objects)

### A1 — `companybrain-langchain`

Two main exports:

```python
# Drop-in retriever for any langchain chain
from companybrain.langchain import BrainRetriever

retriever = BrainRetriever(workspace_id="ws_uuid", mcp_url="http://...")
# Now use anywhere a langchain BaseRetriever is expected:
chain = create_retrieval_chain(llm, retriever, prompt)


# Drop-in memory for langchain agents
from companybrain.langchain import BrainMemory

memory = BrainMemory(workspace_id="ws_uuid")
agent = initialize_agent(tools, llm, memory=memory)
```

Internally: each `retriever.get_relevant_documents(query)` call → MCP `query_brain` → returns brain answer + cited entities → wrapped as langchain `Document` objects.

Plus: `BrainTool` exposing the brain as a langchain `Tool` (so agents that PREFER tools over retrievers can use it):

```python
from companybrain.langchain import BrainTool
tools = [BrainTool(workspace_id="ws_uuid"), other_tools...]
```

### A2 — `companybrain-crewai`

```python
from companybrain.crewai import BrainMemory, BrainTool

agent = Agent(
    role="Senior Engineer",
    goal="Refactor the payment module",
    memory=BrainMemory(workspace_id="ws_uuid"),
    tools=[BrainTool(workspace_id="ws_uuid")],
    backstory="...",
)
```

CrewAI's memory is conversation-scoped today; the brain extends it with codebase-grounded context the agent can pull anytime.

### A3 — `companybrain-autogen`

```python
from companybrain.autogen import BrainMemory, register_brain_tool

memory = BrainMemory(workspace_id="ws_uuid")
register_brain_tool(my_assistant_agent, workspace_id="ws_uuid")
# Now the assistant can call query_brain via AutoGen's tool_use protocol
```

### A4 — `companybrain-openai`

For developers using the OpenAI SDK directly without a framework:

```python
from companybrain.openai import brain_tool_spec

response = openai.chat.completions.create(
    model="gpt-4o",
    messages=[{"role": "user", "content": "What does ..."}],
    tools=[brain_tool_spec(workspace_id="ws_uuid")],
)
# Handle tool_calls → automatic dispatch to brain MCP
```

Plus a `handle_brain_tool_calls(response)` helper that auto-dispatches the brain tool calls and returns the result for the next turn.

### Distribution strategy

Single PyPI package `companybrain-integrations` with sub-modules per framework:

```python
pip install companybrain-integrations[langchain]   # only pulls langchain dep
pip install companybrain-integrations[crewai]
pip install companybrain-integrations[autogen]
pip install companybrain-integrations[openai]
pip install companybrain-integrations[all]
```

Each framework dep is **optional** — installing one doesn't pull all four.

### Open-source the integrations package

The `companybrain-integrations` package is **open-source under Apache 2.0**, separate from the main brain. Reasons:

1. Framework integrations should be auditable + extensible by the community.
2. Customers can self-host the integrations even if their brain instance is on-prem.
3. Establishes credibility in the agent ecosystem (open-source signals collaborator, not extractor).
4. Per LEGAL-CONTEXTDB-INTEGRATION.md, our main brain stays proprietary; only the thin shim is open.

Repo: `github.com/yourorg/companybrain-integrations`. Separate from the main brain repo.

---

## File ownership for THIS PR (parallel-safe)

```
companybrain-integrations/                                        # NEW SEPARATE REPO
companybrain-integrations/pyproject.toml
companybrain-integrations/README.md
companybrain-integrations/LICENSE                                  # Apache 2.0
companybrain-integrations/src/companybrain/__init__.py             # namespace package
companybrain-integrations/src/companybrain/mcp_client.py           # shared HTTP/stdio MCP client
companybrain-integrations/src/companybrain/langchain/
companybrain-integrations/src/companybrain/langchain/__init__.py
companybrain-integrations/src/companybrain/langchain/retriever.py
companybrain-integrations/src/companybrain/langchain/memory.py
companybrain-integrations/src/companybrain/langchain/tool.py
companybrain-integrations/src/companybrain/crewai/
companybrain-integrations/src/companybrain/crewai/__init__.py
companybrain-integrations/src/companybrain/crewai/memory.py
companybrain-integrations/src/companybrain/crewai/tool.py
companybrain-integrations/src/companybrain/autogen/
companybrain-integrations/src/companybrain/autogen/__init__.py
companybrain-integrations/src/companybrain/autogen/memory.py
companybrain-integrations/src/companybrain/autogen/register.py
companybrain-integrations/src/companybrain/openai/
companybrain-integrations/src/companybrain/openai/__init__.py
companybrain-integrations/src/companybrain/openai/tool_spec.py
companybrain-integrations/src/companybrain/openai/handlers.py
companybrain-integrations/tests/
companybrain-integrations/tests/test_langchain_retriever.py
companybrain-integrations/tests/test_crewai_memory.py
companybrain-integrations/tests/test_autogen_register.py
companybrain-integrations/tests/test_openai_tool_call.py
companybrain-integrations/examples/
companybrain-integrations/examples/{langchain_qa.py, crewai_team.py, autogen_pair.py, openai_function.py}
companybrain-integrations/.github/workflows/publish.yml             # auto-publish to PyPI on tag

# In the main brain repo (read-only changes):
docs/INTEGRATIONS.md                                                # NEW — links the integrations repo + examples
```

NO changes to the main `companybrain` package or any file owned by ADR-0055-0067 OR 0069.

---

## Acceptance test (in the new repo)

```python
async def test_langchain_retriever_returns_documents():
    """BrainRetriever returns langchain Document objects with brain-cited content."""
    from langchain_core.documents import Document
    from companybrain.langchain import BrainRetriever

    retriever = BrainRetriever(workspace_id=test_ws, mcp_url=mock_mcp_url)
    docs = retriever.get_relevant_documents("what does foo do?")
    assert all(isinstance(d, Document) for d in docs)
    assert any("foo" in d.page_content.lower() for d in docs)


async def test_langchain_chain_end_to_end():
    """A simple RAG chain using BrainRetriever produces an answer."""
    from langchain_anthropic import ChatAnthropic
    from langchain.chains import create_retrieval_chain
    from companybrain.langchain import BrainRetriever

    retriever = BrainRetriever(workspace_id=test_ws)
    llm = ChatAnthropic(model="claude-haiku-4-5-20251001")
    chain = create_retrieval_chain(llm, retriever)
    result = await chain.ainvoke({"input": "explain the codebase architecture"})
    assert "input" in result and "answer" in result


async def test_crewai_agent_uses_brain_memory():
    """A CrewAI agent with BrainMemory queries the brain mid-task."""
    from crewai import Agent
    from companybrain.crewai import BrainMemory

    agent = Agent(role="...", memory=BrainMemory(workspace_id=test_ws), ...)
    response = agent.execute_task("...")
    # Assert brain.query was invoked
    assert mock_mcp_server.call_count("query_brain") >= 1


async def test_openai_function_dispatch():
    """OpenAI Tool spec dispatched correctly through helper."""
    from companybrain.openai import brain_tool_spec, handle_brain_tool_calls

    response = mock_openai_response_with_tool_call("query_brain", {"question": "..."})
    result = await handle_brain_tool_calls(response, workspace_id=test_ws)
    assert "summary_md" in result


async def test_install_optional_extra_doesnt_pull_others():
    """`pip install companybrain-integrations[langchain]` doesn't pull crewai/autogen."""
    # tested via tox or in CI; assert sys.modules after import
    pass
```

---

## Effort estimate

4 days total, parallelisable to 1.5 days with 4 sessions (one per framework):

| Workstream | Days |
|---|---|
| Repo scaffolding + shared MCP client | 0.5 |
| LangChain adapter | 1 |
| CrewAI adapter | 1 |
| AutoGen adapter | 1 |
| OpenAI tools adapter | 0.5 |

---

## Action items

1. [ ] Create new GitHub repo `companybrain-integrations` (Apache 2.0).
2. [ ] Scaffold pyproject.toml with optional extras per framework.
3. [ ] `mcp_client.py` — shared HTTP/stdio MCP client with auth + retry.
4. [ ] LangChain: BrainRetriever, BrainMemory, BrainTool.
5. [ ] CrewAI: BrainMemory, BrainTool.
6. [ ] AutoGen: BrainMemory, register_brain_tool helper.
7. [ ] OpenAI: brain_tool_spec, handle_brain_tool_calls.
8. [ ] Examples directory (4 working scripts, one per framework).
9. [ ] CI: GitHub Actions to publish on tag to PyPI under `companybrain-integrations`.
10. [ ] Acceptance: 5 tests above PASS.
11. [ ] `docs/INTEGRATIONS.md` in main brain repo links the integrations repo + walks through quickstart per framework.
12. [ ] Add `THIRD-PARTY-INSPIRATIONS.md` entry crediting ContextDB's pattern (per LEGAL doc).
13. [ ] Announce on X/HN/AI Twitter when v0.1.0 ships — coordinated GTM moment.
