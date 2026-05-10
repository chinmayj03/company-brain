# Implementation Prompt — ADR-0052 (harness extensions, phases P5–P7)

**You are landing the harness ecosystem on top of the foundational HarnessLoop. This is the SECOND of two Claude Code sessions for the harness migration. Your session covers phases P5–P7 of ADR-0052 (~17 days of work, 3 PRs sequentially under one coordinated branch). The other session lands ADR-0051 (P1–P4: foundation, sub-agents, skills, hooks/permissions/streaming) — your work assumes that's already on `main` (or coexists behind the same `BRAIN_USE_HARNESS` flag).**

---

## Pre-flight

1. Read `docs/adrs/ADR-0052-comprehensive-feature-adoption.md` start-to-finish — you're delivering features ✓-marked for phases P5/P6/P7.
2. Read `docs/adrs/ADR-0051-agentic-harness-migration.md` §"Decision" so you understand the harness primitives (HarnessLoop, sub-agents, skills, hooks, permissions) you'll build on.
3. **Prerequisite ADR (must be on `main` first):** ADR-0051 (all four phases P1–P4). Verify:
   ```bash
   git log --oneline main | head -200 | grep -E "ADR-0051 P[1-4]" | wc -l
   # Expect output: 4 (one merge commit per phase)
   ```
   If P4 hasn't merged yet, **wait** — your work depends on P1's HarnessLoop, P2's sub-agents, P3's skills/memory, and P4's hooks/permissions/SSE streaming.
4. Also verify ADR-0048/0049/0050 are merged (transitive prereqs of ADR-0051).
5. Create the coordinated branch: `git checkout -b feature/adr-0052-harness-extensions` from `main`.
6. You will land **three sub-PRs off this branch** in sequence — one per phase.

---

## File ownership for THIS coordinated branch (do not touch anything else; ADR-0051's session owns the foundation)

You exclusively own and may CREATE / MODIFY:

```
src/companybrain/harness/commands/                  # P5 — slash commands
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

src/companybrain/harness/mcp_server.py              # P5 — brain-as-MCP
src/companybrain/harness/workspace.py               # P5 — Workspace dataclass
src/companybrain/harness/worktree.py                # P5 — per-job git worktrees
src/companybrain/harness/tools/run_repo_command.py  # P5 — sandboxed bash
src/companybrain/harness/tools/web_fetch.py         # P5
src/companybrain/harness/tools/web_search.py        # P5
src/companybrain/harness/tools/git_branch_diff.py   # P5
src/companybrain/harness/rooms.py                   # P5 — typed surfaces

src/companybrain/sdk/                                # P5 — Python SDK
src/companybrain/sdk/__init__.py
src/companybrain/sdk/client.py
src/companybrain/sdk/models.py

src/companybrain/harness/scheduler.py               # P6 — APScheduler-backed
src/companybrain/harness/plugins.py                 # P6 — marketplace
src/companybrain/harness/notebook_chunker.py        # P6 — .ipynb support
src/companybrain/harness/image_extractor.py         # P6 — vision diagrams
src/companybrain/harness/notes.py                   # P6 — per-entity sticky notes
src/companybrain/harness/subagents/browser_verifier.py  # P6 — adapted A2

ide/vscode-extension/                                # P7 — VS Code extension
ide/vscode-extension/package.json
ide/vscode-extension/src/extension.ts
ide/vscode-extension/src/brain-client.ts
ide/jetbrains-plugin/                                # P7 — JetBrains skeleton (defer publishing)

docs/SLASH-COMMANDS.md                              # P5
docs/MCP-SERVER.md                                  # P5
docs/PLUGIN-AUTHORING.md                            # P6
docs/IDE-INTEGRATION.md                             # P7

db/migrations/V12__scheduled_tasks.sql              # P6
db/migrations/V13__entity_notes.sql                 # P6

tests/unit/test_slash_commands.py                   # P5
tests/unit/test_mcp_server.py                       # P5
tests/unit/test_workspace.py                        # P5
tests/unit/test_worktree.py                         # P5
tests/unit/test_scheduler.py                        # P6
tests/unit/test_plugins.py                          # P6
tests/unit/test_notebook_chunker.py                 # P6
tests/acceptance/test_harness_p5_slash_mcp.py       # P5
tests/acceptance/test_harness_p6_marketplace.py     # P6
tests/acceptance/test_harness_p7_ide.py             # P7
```

You may make **append-only** changes to:

```
src/companybrain/harness/loop.py             # register new tools (run_repo_command, web_fetch, etc.)
src/companybrain/harness/permissions.py      # add new tool capability declarations
src/companybrain/cli.py                      # add `brain plugin install`, `brain schedule`, `brain mcp serve`
src/companybrain/api/main.py                 # mount MCP server route
src/companybrain/config.py                   # tunables for new features
pyproject.toml                                # new deps: apscheduler, mcp, etc.
```

Do NOT modify any file owned by ADR-0051's session (the entire `harness/` foundation: `loop.py`, `subagent.py`, `skills.py`, `memory.py`, `hooks.py`, `permissions.py`, `progress.py`, `compaction.py`, `session.py`, `cost.py`). You may extend them via append-only registration but not change their semantics.

---

## Sub-PR sequence

Land three sub-PRs sequentially under `feature/adr-0052-harness-extensions`. Each sub-PR rebases onto its predecessor.

### Sub-PR 1: Phase P5 — Slash commands + MCP + workspace + headless + rooms (~7 days)

**Branch:** `feature/adr-0052-p5-slash-mcp-workspace` (off main → coordinated branch)

**Deliverables:**

- **Slash commands** (`harness/commands/*.md`): each is a markdown file with frontmatter (name, description, args) and a body that's prepended to the user message before the harness loop starts. Initial set:
  - `/extract <endpoint> <method>` — runs the extraction pipeline
  - `/query <question>` — runs a brain query
  - `/verify <urn>` — spawns a VerifierAgent on a single entity
  - `/diff <commit_a> <commit_b>` — shows brain diff between two repo states
  - `/cost` — last-job cost summary
  - `/explain <method_qname>` — natural-language explanation of a method
  - `/wipe` — clears workspace data (with confirm)
  - `/stats` — brain entity counts by type
  - `/init` — bootstrap a new repo's `.brain/BRAIN.md` + `.brain/hooks/`
  - `/skills list` — show available framework skills
- **MCP server** (`harness/mcp_server.py`): brain-as-MCP. Implements `tools/list`, `tools/call`, `resources/list`, `prompts/list` per the MCP spec (`pip install mcp`). Tools exposed (read-only by default): `query_brain`, `read_entity`, `list_entities_by_file`, `find_callers`, `find_dependencies`. Add-on `mcp_writes` capability flag enables write tools.
- **`Workspace` dataclass** (`harness/workspace.py`): consolidates today's scattered `WORKSPACE_ID`, `WORKSPACE_SLUG`, repo path, branch, commit SHA, capabilities, env vars. Single source of truth; passed everywhere via dependency injection.
- **Per-job git worktrees** (`harness/worktree.py`): every extraction job creates a `git worktree add` for the target commit so concurrent jobs don't fight over `HEAD`. Cleaned up on job complete.
- **Sandboxed bash tool** (`harness/tools/run_repo_command.py`): bubble-wrap-isolated, time-limited (30s default, configurable), output-truncated (10 KB). For `mvn test`, `git log`, `npm run build`, `psql -c '\d table'`. Subject to permission model.
- **WebFetch / WebSearch tools** for sub-agents enriching BusinessContext with framework documentation. Permission: ask in interactive mode, deny by default in non-interactive unless `--allow-net` flag.
- **`git_branch_diff(branch_a, branch_b)` tool**: extract only entities affected by a branch's changes. Massive cost win on large repos (extract a 5-file PR for $0.001 instead of $0.05).
- **Settings hierarchy**: `~/.brain/settings.json` (user) > `.brain/settings.json` (repo) > `BRAIN_ENTERPRISE_CONFIG_URL` (org). Resolved with deep merge in `Workspace.load()`.
- **Headless mode**: `brain extract --headless --json` for CI. Exit code reflects extraction success + drift detection. JSON output is pipe-able.
- **CLI + Python SDK**: `companybrain` package (`from companybrain import CompanyBrain`). `brain = CompanyBrain(repo); result = await brain.extract(endpoint, method)`. CLI is thin wrapper around the SDK.
- **Output JSON format**: every CLI command supports `--output json` for machine consumption.
- **Multi-pane rooms** (`harness/rooms.py`): typed surfaces — `code:`, `db:`, `git:`, `api:` (running service), `docs:` (markdown / ADRs), `metrics:`. Each room exposes a typed query interface; sub-agents pick which to query.

**Acceptance test (`tests/acceptance/test_harness_p5_slash_mcp.py`):**

```python
async def test_slash_commands_route_to_correct_handler():
    """All 10 slash commands resolve and execute their wrapped action."""
    for cmd in ["extract", "query", "verify", "diff", "cost",
                "explain", "wipe", "stats", "init", "skills"]:
        result = await harness_run(f"/{cmd} --dry-run")
        assert result.command_routed == cmd


async def test_mcp_server_responds_to_external_query():
    """Spin up the MCP server, connect a test MCP client, list+call tools."""
    async with start_mcp_server(repo="fixtures/...") as server:
        client = MCPTestClient(server.url)
        tools = await client.list_tools()
        assert "query_brain" in {t.name for t in tools}
        result = await client.call_tool("query_brain", {"question": "what tables does getPayerCompetitors read?"})
        assert "competitive_payer_plan" in result.content


async def test_worktree_isolation():
    """Two concurrent extract jobs against the same repo at different commits
    must not corrupt each other's HEAD."""
    job_a, job_b = await asyncio.gather(
        run_pipeline_harness(commit="abc123"),
        run_pipeline_harness(commit="def456"),
    )
    assert job_a.success and job_b.success


async def test_git_branch_diff_extracts_only_changed():
    """A 5-file PR's worth of work costs ≤ $0.005 (vs $0.05 for full repo)."""
    result = await harness_run("/extract --branch-diff main...feature/x")
    assert result.telemetry["files_extracted"] <= 8
    assert result.telemetry["total_cost_usd"] < 0.005


async def test_headless_json_output():
    """CLI exits with structured JSON suitable for piping."""
    proc = await run_cli(["brain", "extract", "--headless", "--json", ...])
    parsed = json.loads(proc.stdout)
    assert "telemetry" in parsed
    assert proc.returncode == 0
```

**PR description for sub-PR 1:**

```
feat(harness): slash commands + MCP server + workspace + rooms (ADR-0052 P5)

Adds:
- 10 slash commands in harness/commands/*.md
- MCP server (company-brain as MCP) for IDE/external integration
- Workspace dataclass consolidating scattered config
- Per-job git worktrees for concurrent-job isolation
- Sandboxed bash tool, web_fetch, web_search, git_branch_diff
- Settings hierarchy (user > repo > enterprise)
- Headless mode with --json output for CI
- Python SDK alongside CLI
- Multi-pane rooms (code:/db:/git:/api:/docs:/metrics:)

Acceptance: 10 commands route correctly; MCP server responds to external
clients; concurrent worktrees isolated; branch-diff extract < $0.005;
headless JSON pipes.
```

### Sub-PR 2: Phase P6 — Marketplace + scheduled + notebook + image + verifier + notes (~5 days)

**Branch:** `feature/adr-0052-p6-marketplace-and-ecosystem` (rebases onto P5)

**Deliverables:**

- **Plugin marketplace** (`harness/plugins.py`): `brain plugin install <name-or-url>` fetches a bundle (skills + hooks + commands + custom tools). Bundle format: zip containing `plugin.json` (manifest) + `skills/` + `hooks/` + `commands/` + `tools/`. Installed plugins live in `~/.brain/plugins/<name>/`. Trust model: signed manifests + per-plugin permission grants (ask user before install).
- **Scheduled tasks** (`harness/scheduler.py`): `brain schedule daily-rebuild --repo X --endpoint Y --cron "0 2 * * *"`. APScheduler-backed; persists in Postgres `scheduled_tasks` table (migration V12). `brain schedule list/cancel/run-now <id>`.
- **Notebook support** (`harness/notebook_chunker.py`): `.ipynb` chunker — extracts per-cell entities; cell type (code/markdown/raw) becomes a chunk attribute; useful for ML repos.
- **Image support** (`harness/image_extractor.py`): vision-extract `docs/*.{png,svg,jpg}` architecture diagrams via the multimodal model; emit as `Artifact` with `kind="diagram"`; relate to extracted entities by name match.
- **Browser-verifier sub-agent** (`harness/subagents/browser_verifier.py` — adapted A2): when a frontend repo is also in the workspace, the verifier launches a headless Chrome (Playwright), exercises the frontend, and verifies its network calls match the backend's `ApiEndpoint` entities. Surfaces drift.
- **Per-entity notes** (`harness/notes.py`): `brain note add <urn> "Adam said this is being deprecated"` — sticky notes alongside auto-extracted context. Migration V13 adds `entity_notes` table. Notes auto-surface in `/query` responses for any cited entity.
- **Artifact pinning**: extend the existing entity model with `pinned: bool` and `proposed: bool` flags. Pinned entities are excluded from auto-overwrite. Proposed entities require human approval before promotion.

**Acceptance test (`tests/acceptance/test_harness_p6_marketplace.py`):**

```python
async def test_plugin_install_and_use():
    """Install a fixture plugin (acme-spring-boot), verify its skill loads."""
    await run_cli(["brain", "plugin", "install", "fixtures/plugins/acme-spring-boot.zip"])
    result = await run_pipeline_harness(repo="fixtures/spring-boot-sample", ...)
    assert result.skill_loaded == "acme-spring-boot"   # plugin's skill overrides bundled


async def test_scheduled_task_persists_and_runs():
    task_id = await run_cli(["brain", "schedule", "daily-rebuild",
                             "--repo", "...", "--endpoint", "...",
                             "--cron", "* * * * *"])   # every minute for test
    await asyncio.sleep(70)
    result = await get_last_run(task_id)
    assert result.success


async def test_notebook_extraction():
    """Synthetic .ipynb with 3 code cells → 3 entities."""
    result = await run_pipeline_harness(repo="fixtures/ml-notebook-sample", ...)
    assert result.entity_count_by_type["NotebookCell"] == 3


async def test_diagram_artifact_extraction():
    """Synthetic docs/architecture.png with labelled boxes → Artifact + relations."""
    result = await run_pipeline_harness(repo="fixtures/repo-with-diagram", ...)
    artifacts = [a for a in result.artifacts if a.kind == "diagram"]
    assert len(artifacts) >= 1


async def test_per_entity_notes_surface_in_query():
    await run_cli(["brain", "note", "add",
                   "urn:cb:dev:code:network-iq:method:Foo.bar",
                   "Deprecated 2026-Q4"])
    response = await run_cli_json(["brain", "query", "--json",
                                    "what does Foo.bar do?"])
    assert "Deprecated 2026-Q4" in response["notes"]
```

**PR description for sub-PR 2:**

```
feat(harness): marketplace + scheduled + notebook + image + verifier + notes (ADR-0052 P6)

Adds:
- Plugin marketplace (brain plugin install) with signed manifests
- APScheduler-backed scheduled tasks (brain schedule)
- Notebook (.ipynb) chunker
- Vision-based diagram extractor for docs/*.{png,svg,jpg}
- Browser-verifier sub-agent (frontend↔brain parity check)
- Per-entity sticky notes (brain note add)
- Artifact pinning (pinned/proposed status)

Migrations: V12 scheduled_tasks, V13 entity_notes.
```

### Sub-PR 3: Phase P7 — IDE integration (~5 days)

**Branch:** `feature/adr-0052-p7-ide-integration` (rebases onto P6)

**Deliverables:**

- **VS Code extension** (`ide/vscode-extension/`):
  - Right-click on a method → "Ask brain" runs `/query` with the qname pre-filled. Response renders in a webview.
  - Sidebar panel: brain's current context for the open file (related entities, edges, BusinessContext).
  - Hover tooltips: hovering a Spring `@Autowired` field shows the bean's risk + invariants from the brain.
  - Status-bar item: "🧠 Brain: 1,247 entities, last extracted 12 min ago"
  - Settings: brain server URL, workspace ID, default endpoint.
- **JetBrains skeleton** (`ide/jetbrains-plugin/`): same backend, IntelliJ Platform plugin scaffold. Defer publishing to JetBrains Marketplace until VS Code version is battle-tested.
- The extension talks to the brain via the **MCP server** built in P5 — no new backend code; this is purely a frontend.
- Build + package: `npm run build` produces `.vsix`; CI uploads to releases page.

**Acceptance test (`tests/acceptance/test_harness_p7_ide.py`):**

```python
def test_vscode_extension_packages_cleanly():
    """vsce package produces a .vsix with no warnings."""
    proc = subprocess.run(["npx", "vsce", "package"], cwd="ide/vscode-extension")
    assert proc.returncode == 0
    assert (Path("ide/vscode-extension") / "company-brain-1.0.0.vsix").exists()


async def test_extension_queries_brain_via_mcp():
    """Headless test: extension's brain-client.ts can call MCP server tools."""
    async with start_mcp_server(repo="fixtures/...") as server:
        result = await run_node_script(
            "ide/vscode-extension/test/headless-query.js",
            env={"MCP_URL": server.url, "QNAME": "Foo.bar"},
        )
        assert "result" in result
```

**PR description for sub-PR 3:**

```
feat(harness): VS Code IDE integration (ADR-0052 P7)

Adds VS Code extension that talks to the brain via the MCP server (built
in P5). Right-click → Ask brain, sidebar context panel, hover tooltips
for Spring annotations.

JetBrains plugin skeleton included; defer marketplace publishing until
VS Code version is battle-tested.

Acceptance: vsce packages cleanly; extension's brain-client successfully
calls MCP server tools.
```

---

## Coordinated branch merge

After all three sub-PRs merge into `feature/adr-0052-harness-extensions`:

```bash
git checkout main
git merge --no-ff feature/adr-0052-harness-extensions \
  -m "feat(harness): land ADR-0052 P5–P7 (slash + MCP + marketplace + IDE)"
git push
```

After this lands AND the combined acceptance suite has been green for two weeks:

```bash
# Flip the default
sed -i 's/use_harness:.*= False/use_harness: bool = True/' src/companybrain/config.py
# Remove legacy orchestrator stage machine
git rm src/companybrain/pipeline/_orchestrator_legacy.py
```

---

## Verification (run before opening EACH sub-PR)

```bash
.venv/bin/mypy src/companybrain/harness src/companybrain/sdk
.venv/bin/ruff check src/companybrain/harness src/companybrain/sdk
.venv/bin/pytest tests/unit/test_*.py -v
.venv/bin/pytest tests/acceptance/test_harness_pN_*.py -v   # N = phase number
```

For P7 also:

```bash
cd ide/vscode-extension && npm install && npm test && npx vsce package
```

---

## Things to NOT do (these belong to the OTHER session — ADR-0051)

Do not touch:

- `harness/loop.py`, `harness/subagent.py`, `harness/skills.py`, `harness/memory.py`, `harness/hooks.py`, `harness/permissions.py`, `harness/progress.py`, `harness/compaction.py`, `harness/session.py`, `harness/cost.py` (foundation files; you may register tools to `loop.py` and capability declarations to `permissions.py`, append-only)
- The framework SKILL.md files in `frameworks/` (those are in P3 of ADR-0051)
- Acceptance tests `test_harness_p1.py` through `test_harness_p4_full.py`
- `docs/HARNESS.md` and `docs/FEATURE-INDEX.md` (ADR-0051 owns those)

If you need a primitive that doesn't exist in the foundation, file a request as
a follow-up and use a stub for now — do not modify the other session's files.
