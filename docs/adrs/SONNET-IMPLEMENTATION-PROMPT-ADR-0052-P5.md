# Implementation Prompt — ADR-0052 Phase 5 (slash + MCP + workspace + headless + rooms)

**Single-PR Claude Code session. ~7 days. Adds the "agent meets the world" layer: 10 slash commands, MCP server (brain-as-MCP), Workspace dataclass, per-job worktrees, sandboxed bash, web tools, headless mode, Python SDK, multi-pane rooms.**

---

## Pre-flight

1. Read `docs/adrs/ADR-0052-comprehensive-feature-adoption.md` §"Phase 5".
2. Verify ADR-0051 P4 is on `main`:
   ```bash
   git log --oneline main | head -100 | grep -q "ADR-0051 P4" || exit 1
   ```
3. `git checkout -b feature/adr-0052-p5-slash-mcp-workspace`.

---

## File ownership for THIS PR

CREATE / MODIFY exclusively:

```
src/companybrain/harness/commands/__init__.py
src/companybrain/harness/commands/extract.md
src/companybrain/harness/commands/query.md
src/companybrain/harness/commands/verify.md
src/companybrain/harness/commands/diff.md
src/companybrain/harness/commands/cost.md
src/companybrain/harness/commands/explain.md
src/companybrain/harness/commands/wipe.md
src/companybrain/harness/commands/stats.md
src/companybrain/harness/commands/init.md
src/companybrain/harness/commands/skills.md
src/companybrain/harness/mcp_server.py
src/companybrain/harness/workspace.py
src/companybrain/harness/worktree.py
src/companybrain/harness/rooms.py
src/companybrain/harness/tools/run_repo_command.py
src/companybrain/harness/tools/web_fetch.py
src/companybrain/harness/tools/web_search.py
src/companybrain/harness/tools/git_branch_diff.py
src/companybrain/sdk/__init__.py
src/companybrain/sdk/client.py
src/companybrain/sdk/models.py
docs/SLASH-COMMANDS.md
docs/MCP-SERVER.md
tests/unit/test_slash_commands.py
tests/unit/test_mcp_server.py
tests/unit/test_workspace.py
tests/unit/test_worktree.py
tests/acceptance/test_harness_p5_slash_mcp.py
```

APPEND-ONLY to:

```
src/companybrain/harness/loop.py             # register new tools
src/companybrain/harness/permissions.py      # add capability declarations for new tools
src/companybrain/cli.py                      # add `brain mcp serve`, `brain plugin install` (stub)
src/companybrain/api/main.py                 # mount MCP route
src/companybrain/config.py                   # tunables
pyproject.toml                                # add `mcp` dep, `playwright` (optional, for P6)
```

---

## Implementation steps

### 1. Slash commands

`harness/commands/__init__.py` loads all `.md` files; each has YAML frontmatter:

```markdown
---
name: extract
description: Run extraction pipeline for an endpoint
args:
  - name: endpoint
    type: string
    required: true
  - name: method
    type: string
    default: GET
---
You are extracting an endpoint. Use the canonical pipeline:
1. discover_routes
2. find_entry_handler with endpoint={endpoint}, method={method}
3. list_candidate_files
4. spawn_extractor for each file
5. write_to_brain + finalize_brain
```

The command parser strips the `/extract /v1/foo POST` prefix from a user message, fills the template, and prepends to the harness loop's first user message.

Implement all 10 commands listed in the ADR.

### 2. MCP server

`harness/mcp_server.py` using the `mcp` Python SDK:

```python
"""Brain-as-MCP server. External tools (IDEs, Claude Code, ChatGPT) connect
and query the brain via standard MCP tool calls."""
from mcp.server import Server
from mcp.types import Tool, TextContent
import asyncio

from companybrain.retrieval.qdrant_store import QdrantBrainStore
from companybrain.assembly.smart_zone import SmartZoneAssembler


def build_server(workspace_id: str, brain_root: str) -> Server:
    server = Server("company-brain")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(name="query_brain",
                 description="Natural-language query against the brain.",
                 inputSchema={"type": "object",
                              "properties": {"question": {"type": "string"}}}),
            Tool(name="read_entity",
                 description="Read one entity by URN.",
                 inputSchema={"type": "object",
                              "properties": {"urn": {"type": "string"}}}),
            Tool(name="list_entities_by_file", inputSchema={...}),
            Tool(name="find_callers",          inputSchema={...}),
            Tool(name="find_dependencies",     inputSchema={...}),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[TextContent]:
        if name == "query_brain":
            zone = await SmartZoneAssembler(brain_root=brain_root, workspace_slug="dev").assemble(arguments["question"])
            return [TextContent(type="text", text=zone.to_markdown())]
        # ... other tools
        raise ValueError(f"Unknown tool: {name}")

    return server


async def serve(port: int = 8765, **kwargs):
    server = build_server(**kwargs)
    await server.run_sse(port=port)
```

CLI entry: `brain mcp serve --workspace ... --brain-root ...`.

### 3. `harness/workspace.py`

```python
"""Workspace — single source of truth for repo + workspace + capabilities."""
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass(frozen=True)
class Workspace:
    id: str
    slug: str
    repo_path: Path
    branch: str = "main"
    commit_sha: Optional[str] = None
    capabilities: dict = None    # WorkspaceGrants from P4

    @classmethod
    def load(cls, repo_path: Path) -> "Workspace":
        """Resolve from settings hierarchy: ~/.brain/settings.json (user)
        > .brain/settings.json (repo) > BRAIN_ENTERPRISE_CONFIG_URL (org)."""
        ...
```

### 4. `harness/worktree.py`

```python
"""Per-job git worktree management. Concurrent extractions don't fight
over HEAD."""
import asyncio
from pathlib import Path
import tempfile


class WorktreeManager:
    def __init__(self, repo_path: Path, commit_sha: str):
        self.repo_path = repo_path
        self.commit_sha = commit_sha
        self._wt_path: Path | None = None

    async def __aenter__(self) -> Path:
        self._wt_path = Path(tempfile.mkdtemp(prefix="brain-wt-"))
        await asyncio.create_subprocess_exec(
            "git", "worktree", "add", "--detach",
            str(self._wt_path), self.commit_sha,
            cwd=self.repo_path,
        )
        return self._wt_path

    async def __aexit__(self, *exc):
        await asyncio.create_subprocess_exec(
            "git", "worktree", "remove", "--force", str(self._wt_path),
            cwd=self.repo_path,
        )
```

### 5. New tools

- `tools/run_repo_command.py` — bubblewrap-isolated bash with timeout + output truncation. Capability: `EXEC_SHELL`.
- `tools/web_fetch.py` — capability `NETWORK`. Fetches a URL, returns truncated text. Subject to `--allow-net` flag in non-interactive mode.
- `tools/web_search.py` — capability `NETWORK`. Wraps any search backend (DuckDuckGo HTML or Google CSE).
- `tools/git_branch_diff.py` — wraps `git diff --name-only branch_a...branch_b`; returns the changed files list, the harness then narrows extraction to those.

### 6. `harness/rooms.py` — typed surfaces

```python
"""Typed surfaces that sub-agents query.

`code:foo.java`     → file content via FileCache
`db:nodes`          → live Postgres query
`git:log:HEAD~5`    → git log results
`api:GET /health`   → curl the running service
`docs:ADR-0051`     → markdown file content
`metrics:cost:24h`  → telemetry timeseries
"""
class Rooms:
    async def query(self, room_uri: str) -> str:
        scheme, _, path = room_uri.partition(":")
        return await self._handlers[scheme](path)
```

### 7. Python SDK

```python
# sdk/client.py
class CompanyBrain:
    def __init__(self, *, repo: str, workspace: str = "dev", api_url: str = "http://localhost:8000"):
        ...
    async def extract(self, endpoint: str, method: str = "GET") -> RunResult: ...
    async def query(self, question: str) -> QueryResponse: ...
    async def diff(self, branch_a: str, branch_b: str) -> DiffResult: ...
```

### 8. Headless mode + JSON output

CLI: `brain extract --headless --json` returns structured JSON; exit code 0/1/2 for success/extraction-error/drift-detected.

---

## Acceptance test

```python
@pytest.mark.asyncio
async def test_slash_commands_route_correctly():
    for cmd in ["extract","query","verify","diff","cost","explain","wipe","stats","init","skills"]:
        result = await harness_run(f"/{cmd} --dry-run")
        assert result.command_routed == cmd


@pytest.mark.asyncio
async def test_mcp_server_responds_to_external_query():
    async with start_mcp_server(repo="fixtures/...") as srv:
        client = MCPTestClient(srv.url)
        tools = await client.list_tools()
        assert "query_brain" in {t.name for t in tools}
        out = await client.call_tool("query_brain", {"question": "what tables does getPayerCompetitors read?"})
        assert "competitive_payer_plan" in out


@pytest.mark.asyncio
async def test_concurrent_worktrees_isolated():
    a, b = await asyncio.gather(
        run_pipeline_harness(commit="abc123"),
        run_pipeline_harness(commit="def456"),
    )
    assert a.success and b.success


@pytest.mark.asyncio
async def test_branch_diff_extract_under_005():
    result = await harness_run("/extract --branch-diff main...feature/x")
    assert result.telemetry["files_extracted"] <= 8
    assert result.telemetry["total_cost_usd"] < 0.005


@pytest.mark.asyncio
async def test_headless_json_output(tmp_path):
    proc = await run_cli(["brain","extract","--headless","--json","..."], capture=True)
    parsed = json.loads(proc.stdout)
    assert "telemetry" in parsed and proc.returncode == 0
```

---

## PR description

```
feat(harness): slash + MCP + workspace + rooms + headless + SDK (ADR-0052 P5)

Adds:
- 10 slash commands (.brain/commands/*.md template-based)
- MCP server (brain-as-MCP for IDE/external integration)
- Workspace dataclass consolidating scattered config
- Per-job git worktrees (concurrent jobs no longer fight over HEAD)
- run_repo_command (sandboxed bash), web_fetch, web_search, git_branch_diff
- Settings hierarchy (~/.brain > .brain > BRAIN_ENTERPRISE_CONFIG_URL)
- Headless mode with --json output for CI
- Python SDK alongside the CLI
- Multi-pane rooms (code:/db:/git:/api:/docs:/metrics:)

Acceptance: 10 commands route; MCP server responds; concurrent worktrees
isolated; branch-diff extract < $0.005; headless JSON pipes cleanly.
```
