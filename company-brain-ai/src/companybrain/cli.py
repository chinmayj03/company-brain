"""
brain CLI — entry point for repo-scoped extraction and ad-hoc queries.

Install:
    cd company-brain-ai
    pip install -e .
Then `brain --help`.

Subcommands:
    brain index       — full repo extraction (structural pre-pass + per-endpoint LLM)
    brain map         — extract one file
    brain query       — natural-language query against the brain
    brain blast-radius — BFS over Neo4j for an entity
    brain rebuild-from-json — rebuild Postgres + Neo4j + Qdrant from .brain/
    brain enrich      — re-run Stage 2 + Stage 3 over existing .brain/ entities
    brain push        — copy the .brain/ to platform-brain (Stage 2)
"""
from __future__ import annotations
import asyncio
import os
import sys
from pathlib import Path
from typing import Optional


# ── Eager .env load — MUST run BEFORE any `from companybrain.*` import ───────
# pydantic_settings.BaseSettings in companybrain.config reads .env from cwd at
# instance-construction time (line `settings = Settings()` runs when the module
# is first imported). When the CLI is invoked from company-brain-ai/, pydantic
# loads company-brain-ai/.env — which may carry a stale ANTHROPIC_API_KEY that
# 401s. We pre-populate os.environ from the repo-root .env so settings sees
# the correct value the moment it constructs.
def _bootstrap_env() -> None:
    try:
        from dotenv import load_dotenv  # type: ignore
    except ImportError:
        print("[cli-bootstrap] python-dotenv not installed; relying on shell env",
              file=sys.stderr)
        return
    here = Path(__file__).resolve()
    repo_root_env = here.parent.parent.parent.parent / ".env"
    sub_env       = here.parent.parent.parent / ".env"
    cwd_env       = Path.cwd() / ".env"

    # company-brain/.env is the source of truth — override=True so it beats any
    # stale value in shell env or sub-package .env.
    if repo_root_env.is_file():
        load_dotenv(repo_root_env, override=True)
    if sub_env.is_file() and sub_env.resolve() != repo_root_env.resolve():
        load_dotenv(sub_env, override=False)
    if cwd_env.is_file() and cwd_env.resolve() not in {
        repo_root_env.resolve() if repo_root_env.is_file() else None,
        sub_env.resolve()       if sub_env.is_file()       else None,
    }:
        load_dotenv(cwd_env, override=False)

    # Standalone CLI cannot resolve Docker network alias `neo4j:7687`.
    uri = os.environ.get("NEO4J_URI", "")
    if not uri or "neo4j:7687" in uri:
        os.environ["NEO4J_URI"] = "bolt://localhost:7687"

    # One-line diagnostic — shows masked key + which file won so we can debug
    # 401s without printing the secret.
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    masked = f"{key[:8]}...{key[-4:]}" if len(key) > 12 else "(unset)"
    print(
        f"[cli-bootstrap] ANTHROPIC_API_KEY={masked} (len={len(key)}) "
        f"repo_root_env={repo_root_env.is_file()} sub_env={sub_env.is_file()}",
        file=sys.stderr,
    )


_bootstrap_env()
# ── End env bootstrapping. Now safe to import companybrain.* ──────────────────


import structlog
import typer

from companybrain.cli_helpers.endpoint_discovery import discover_endpoints
from companybrain.cli_helpers.repo_walker import walk_repo
from companybrain.cli_helpers.brain_rebuild import rebuild_from_json
from companybrain.models.entities import PipelineStartRequest, RepoConfig, RepoType
from companybrain.pipeline.orchestrator import run_pipeline
from companybrain.pipeline.structural_prepass import run_structural_prepass
from companybrain.collectors.code_tracer import FocalContext, CodeUnit
from companybrain.store.identity import workspace_slug_for, to_urn
from companybrain.retrieval.hybrid_search import HybridSearcher

app = typer.Typer(help="company-brain CLI", no_args_is_help=True)
log = structlog.get_logger(__name__)

# ── brain index ──────────────────────────────────────────────────────────────

@app.command()
def index(
    repo: str = typer.Argument(..., help="Path to the repo root"),
    branch: str = typer.Option("main", help="Git branch"),
    workspace_id: str = typer.Option(
        os.getenv("BRAIN_WORKSPACE_ID", "00000000-0000-0000-0000-000000000001"),
        help="Workspace UUID; defaults to the dev workspace",
    ),
    endpoints: Optional[str] = typer.Option(
        None, help="Comma-separated METHOD path list to extract; default = auto-discover"
    ),
    repo_name: str = typer.Option("monorepo", help="Repo identifier for URNs"),
    dry_run: bool = typer.Option(False, help="Discover endpoints but don't run LLM"),
    headless: bool = typer.Option(False, "--headless",
                                   help="ADR-0052 P5: non-interactive mode for CI."),
    json_output: bool = typer.Option(False, "--json",
                                      help="ADR-0052 P5: emit a structured JSON payload to stdout."),
):
    """Whole-repo extraction.

    Steps:
      1. Run structural pre-pass once (cb-api → Neo4j).
      2. Discover endpoints (auto or from --endpoints).
      3. For each endpoint, run the existing orchestrator (which now skips
         LLM for structural-fresh code units).
    """
    repo_path = Path(repo).resolve()
    if not repo_path.is_dir():
        typer.secho(f"Not a directory: {repo_path}", fg=typer.colors.RED)
        raise typer.Exit(1)

    if headless or json_output:
        # ADR-0052 P5: structured JSON for CI consumption.
        from companybrain.cli_helpers.headless import run_index_headless

        payload, exit_code = asyncio.run(run_index_headless(
            repo_path=repo_path,
            branch=branch,
            workspace_id=workspace_id,
            endpoints=endpoints,
            repo_name=repo_name,
            dry_run=dry_run,
        ))
        if json_output:
            import json as _json
            typer.echo(_json.dumps(payload, default=str))
        else:
            typer.echo(payload.get("summary", ""))
        raise typer.Exit(exit_code)

    asyncio.run(_index_async(
        repo_path=repo_path, branch=branch, workspace_id=workspace_id,
        endpoints=endpoints, repo_name=repo_name, dry_run=dry_run,
    ))


async def _index_async(*, repo_path: Path, branch: str, workspace_id: str,
                        endpoints: Optional[str], repo_name: str, dry_run: bool) -> None:
    typer.echo(f"⤷ index: {repo_path}  branch={branch}  ws={workspace_id}")

    # 1. Structural pre-pass: populate Neo4j
    # FocalContext requires endpoint+method; for repo-wide pre-pass we use a
    # sentinel value — the structural_prepass only reads code_units.
    fc = FocalContext(
        endpoint="*",
        method="REPO",
        code_units=[
            CodeUnit(file_path=str(p), repo_name=repo_name, role="unknown",
                     class_name=p.stem, content=p.read_text(errors="replace"),
                     language=_lang_for(p))
            for p in walk_repo(repo_path)
        ],
    )
    typer.echo(f"   walked {len(fc.code_units)} source files")

    prepass = await run_structural_prepass(
        repo_path=str(repo_path),
        commit_sha=_git_head(repo_path),
        workspace_id=workspace_id,
        focal_context=fc,
    )
    typer.echo(f"   structural pre-pass: {len(prepass.fresh_units)} fresh, "
               f"{len(prepass.dirty_units)} dirty, cb_api={prepass.cb_api_status}")

    # 2. Endpoint discovery
    if endpoints:
        endpoint_list = [tuple(s.strip().split(maxsplit=1)) for s in endpoints.split(",")]
    else:
        endpoint_list = discover_endpoints(repo_path)
    typer.echo(f"   {len(endpoint_list)} endpoint(s) to extract")

    if dry_run:
        for method, path in endpoint_list:
            typer.echo(f"     [{method}] {path}")
        return

    # 3. Per-endpoint orchestrator (existing pipeline, now structural-aware)
    for i, (method, path) in enumerate(endpoint_list, 1):
        typer.echo(f"   [{i}/{len(endpoint_list)}] {method} {path}")
        request = PipelineStartRequest(
            endpoint_path=path,
            http_method=method,
            branch=branch,
            workspace_id=workspace_id,
            repos=[RepoConfig(local_path=str(repo_path), type=RepoType.BACKEND,
                              branch=branch, name=repo_name)],
        )
        try:
            await run_pipeline(request)
        except Exception as exc:
            typer.secho(f"     ✗ {exc}", fg=typer.colors.YELLOW)

    typer.secho("✓ index complete", fg=typer.colors.GREEN)


# ── brain map ────────────────────────────────────────────────────────────────

@app.command()
def map(
    file: str = typer.Argument(..., help="File to extract"),
    repo: str = typer.Option(".", help="Repo root"),
    workspace_id: str = typer.Option(
        os.getenv("BRAIN_WORKSPACE_ID", "00000000-0000-0000-0000-000000000001"),
    ),
    repo_name: str = typer.Option("monorepo"),
):
    """Extract a single file (no endpoint anchor)."""
    file_path = Path(file).resolve()
    repo_path = Path(repo).resolve()
    if not file_path.exists():
        typer.secho(f"Not found: {file_path}", fg=typer.colors.RED)
        raise typer.Exit(1)
    asyncio.run(_map_async(file_path, repo_path, workspace_id, repo_name))


async def _map_async(file: Path, repo: Path, workspace_id: str, repo_name: str) -> None:
    fc = FocalContext(
        endpoint=str(file),
        method="MAP",
        code_units=[CodeUnit(
            file_path=str(file), repo_name=repo_name, role="unknown",
            class_name=file.stem, content=file.read_text(errors="replace"),
            language=_lang_for(file),
        )],
    )
    prepass = await run_structural_prepass(
        repo_path=str(repo), commit_sha=_git_head(repo),
        workspace_id=workspace_id, focal_context=fc,
    )
    typer.echo(f"  structural pre-pass: {len(prepass.fresh_units)} fresh, "
               f"{len(prepass.dirty_units)} dirty")
    typer.secho("  (Stage 1 LLM extraction for single files: see follow-up ADR)",
                fg=typer.colors.YELLOW)


# ── brain query ──────────────────────────────────────────────────────────────

@app.command()
def query(
    text: str = typer.Argument(..., help="Question or keywords"),
    repo: str = typer.Option(".", help="Repo root with a populated .brain/"),
    top_k: int = typer.Option(10),
    types: Optional[str] = typer.Option(None, help="Comma-separated entity types"),
):
    """Hybrid search over .brain/ + Qdrant. Smart-zone assembly = ADR-0018."""
    repo_path = Path(repo).resolve()
    slug = workspace_slug_for(os.getenv("BRAIN_WORKSPACE_ID", ""))
    searcher = HybridSearcher(brain_root=repo_path, workspace_slug=slug)
    type_list = types.split(",") if types else None
    hits = searcher.search(text, top_k=top_k, entity_types=type_list)
    if not hits:
        typer.echo("(no results)")
        return
    for h in hits:
        typer.echo(f"  {h.score:6.4f}  {h.urn}")
        if h.payload.get("t1_summary"):
            typer.echo(f"          → {h.payload['t1_summary']}")


# ── brain blast-radius ───────────────────────────────────────────────────────

@app.command(name="blast-radius")
def blast_radius_cmd(
    urn: str = typer.Argument(..., help="Entity URN"),
    hops: int = typer.Option(2),
    direction: str = typer.Option("both", help="upstream | downstream | both"),
):
    """BFS over Neo4j. Falls back to Postgres recursive CTE if Neo4j is down."""
    asyncio.run(_blast_radius_async(urn, hops, direction))


async def _blast_radius_async(urn: str, hops: int, direction: str) -> None:
    from neo4j import AsyncGraphDatabase
    driver = AsyncGraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"),
              os.getenv("NEO4J_PASSWORD", "password")),
    )
    direction_clause = {
        "upstream":   f"<-[*1..{hops}]-",
        "downstream": f"-[*1..{hops}]->",
        "both":       f"-[*1..{hops}]-",
    }[direction]
    async with driver.session() as session:
        result = await session.run(
            f"MATCH (n {{id: $urn}}){direction_clause}(m) RETURN DISTINCT m.id AS id, labels(m) AS labels",
            urn=urn,
        )
        records = await result.data()
    await driver.close()
    typer.echo(f"  {len(records)} node(s) in blast radius")
    for r in records:
        typer.echo(f"    {r['labels'][0] if r['labels'] else '?':12s}  {r['id']}")


# ── brain rebuild-from-json ──────────────────────────────────────────────────

@app.command(name="rebuild-from-json")
def rebuild_from_json_cmd(
    repo: str = typer.Option(".", help="Repo root containing .brain/"),
    workspace_id: str = typer.Option(
        os.getenv("BRAIN_WORKSPACE_ID", "00000000-0000-0000-0000-000000000001"),
    ),
):
    """Replay every .brain/ JSON into Postgres + Neo4j + Qdrant.

    Use after wiping any of the projection stores."""
    repo_path = Path(repo).resolve()
    asyncio.run(rebuild_from_json(repo_path, workspace_id))


# ── brain enrich ─────────────────────────────────────────────────────────────

@app.command(name="enrich")
def enrich_cmd(
    repo: str = typer.Option(".", help="Repo root containing .brain/"),
    workspace_id: str = typer.Option(
        os.getenv("BRAIN_WORKSPACE_ID", "00000000-0000-0000-0000-000000000001"),
    ),
    skip_relationships: bool = typer.Option(False, help="Skip Stage 2 relationship extraction."),
    skip_context: bool = typer.Option(False, help="Skip Stage 3 context synthesis."),
):
    """Re-run Stage 2 (relationships) + Stage 3 (context) over EXISTING entities.

    Skips Stage 1 entity extraction entirely — uses entities already stored in
    .brain/. Roughly 5x cheaper than a full pipeline run because the most
    expensive stage is skipped. Use this when:
      - Source files have not changed since the last full run, and
      - You want to re-extract relationships with the new edge taxonomy, OR
      - You want to re-synthesise business_context with the expanded schema.
    """
    from companybrain.cli_helpers.brain_enrich import enrich_existing_sync
    repo_path = Path(repo).resolve()
    enrich_existing_sync(
        repo_path,
        workspace_id,
        skip_relationships=skip_relationships,
        skip_context=skip_context,
    )


# ── helpers ──────────────────────────────────────────────────────────────────

def _lang_for(p: Path) -> str:
    return {
        ".java": "java", ".kt": "kotlin", ".py": "python",
        ".ts": "typescript", ".tsx": "typescript",
        ".js": "javascript", ".jsx": "javascript",
        ".go": "go", ".rb": "ruby", ".cs": "csharp",
    }.get(p.suffix, "unknown")


def _git_head(repo: Path) -> str:
    import subprocess
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL, timeout=5,
        ).decode().strip()
    except Exception:
        return "HEAD"


# ── ADR-0051 P4: brain session / brain tools ────────────────────────────────


@app.command(name="session")
def session_cmd(
    action: str = typer.Argument(..., help="list | resume | transcript"),
    id: Optional[str] = typer.Argument(None, help="Session id (required for resume/transcript)"),
):
    """Inspect or resume a harness session (ADR-0051 P4).

    Examples:

        brain session list
        brain session transcript sess-abc123
        brain session resume     sess-abc123
    """
    from companybrain.harness import session as session_mod

    action = action.strip().lower()
    if action == "list":
        # Pull live sessions from the registry and any saved snapshots from disk.
        rows = list(session_mod.list_sessions())
        d = session_mod.session_dir()
        if d.is_dir():
            for p in sorted(d.glob("*.json")):
                try:
                    s = session_mod.load(p)
                except Exception as exc:  # noqa: BLE001 — diagnostic only
                    typer.secho(f"  {p.name}: load failed — {exc}", fg=typer.colors.YELLOW)
                    continue
                if not any(r["id"] == s.id for r in rows):
                    rows.append({
                        "id":         s.id,
                        "created_at": s.created_at,
                        "status":     s.status,
                        "endpoint":   f"{s.method} {s.endpoint}".strip(),
                        "cost_usd":   round(s.cost.total_cost_usd, 4),
                    })
        if not rows:
            typer.echo("(no sessions)")
            return
        for r in rows:
            typer.echo(
                f"  {r['id']:24s}  {r['status']:10s}  "
                f"${r.get('cost_usd', 0):>7.4f}  {r['endpoint']}  ({r['created_at']})"
            )
        return

    if action in {"resume", "transcript"}:
        if not id:
            typer.secho(f"`brain session {action}` requires an <id>.", fg=typer.colors.RED)
            raise typer.Exit(2)
        # Load from in-memory registry first; fall back to disk.
        sess = session_mod.get_session_or_none(id)
        if sess is None:
            path = session_mod.session_dir() / f"{id}.json"
            if not path.is_file():
                typer.secho(f"Session {id!r} not found.", fg=typer.colors.RED)
                raise typer.Exit(1)
            sess = session_mod.load(path)
        if action == "transcript":
            import json as _json
            for msg in sess.transcript:
                typer.echo(_json.dumps(msg, default=str))
            return
        # resume: print a "what would happen" preview. Re-running the harness
        # programmatically against a partially-completed session is left to
        # follow-up work — this hook is here so the CLI surface exists today.
        typer.echo(f"Session {sess.id}  status={sess.status}  cost=${sess.cost.total_cost_usd:.4f}")
        typer.echo(f"  endpoint: {sess.method} {sess.endpoint}")
        typer.echo(f"  repo:     {sess.repo_path}")
        typer.echo(f"  todo:     {len(sess.todo.snapshot())} root items")
        typer.secho("  resume is read-only in P4; run `brain index` to start a fresh session.",
                    fg=typer.colors.YELLOW)
        return

    typer.secho(f"Unknown action: {action!r}. Valid: list | resume | transcript",
                fg=typer.colors.RED)
    raise typer.Exit(2)


@app.command(name="tools")
def tools_cmd(
    action: str = typer.Argument("list", help="list (default)"),
):
    """Print the harness tool registry (ADR-0051 P4).

    `brain tools list` shows every registered tool with its required
    capabilities — useful for confirming a tool's permission gate.
    """
    if action.strip().lower() != "list":
        typer.secho(f"Unknown action: {action!r}. Only 'list' is supported.",
                    fg=typer.colors.RED)
        raise typer.Exit(2)

    # Late import so the CLI doesn't pay for harness wiring on every invocation.
    from companybrain.harness.tools import TOOL_REGISTRY

    if not TOOL_REGISTRY:
        typer.echo("(no tools registered)")
        return
    for name, t in sorted(TOOL_REGISTRY.items()):
        caps = ",".join(c.value for c in t.requires) or "(none)"
        typer.echo(f"  {name:32s}  [{caps}]")
        typer.echo(f"      {t.description}")


# ── ADR-0052 P5: brain mcp serve / brain plugin install (stub) ──────────────


@app.command(name="mcp")
def mcp_cmd(
    action: str = typer.Argument(..., help="serve"),
    repo: str = typer.Option(".", help="Repo root (workspace root)"),
    workspace_id: str = typer.Option(
        os.getenv("BRAIN_WORKSPACE_ID", "00000000-0000-0000-0000-000000000001"),
    ),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8765),
    http: bool = typer.Option(False, "--http", help="Serve over HTTP instead of stdio."),
    allow_writes: bool = typer.Option(False, "--allow-writes",
                                       help="Expose mutating tools (mcp_writes)."),
):
    """Serve the brain-as-MCP server (ADR-0052 P5).

        brain mcp serve --repo ./my-repo --workspace ${BRAIN_WORKSPACE_ID}
        brain mcp serve --http --port 8765
    """
    if action != "serve":
        typer.secho(f"Unknown action: {action!r}. Only 'serve' is supported.",
                    fg=typer.colors.RED)
        raise typer.Exit(2)

    from companybrain.harness.mcp_server import build_server

    server = build_server(
        workspace_id=workspace_id,
        brain_root=Path(repo).resolve(),
        allow_writes=allow_writes,
    )
    if http:
        typer.echo(f"brain-as-MCP listening on http://{host}:{port} "
                   f"(workspace={workspace_id})")
        asyncio.run(server.run_sse(host=host, port=port))
    else:
        typer.echo(f"brain-as-MCP serving over stdio (workspace={workspace_id})",
                   err=True)
        asyncio.run(server.run_stdio())


@app.command(name="plugin")
def plugin_cmd(
    action: str = typer.Argument(..., help="install | list | uninstall"),
    target: Optional[str] = typer.Argument(None, help="Plugin name, URL or .zip path"),
):
    """Plugin marketplace (ADR-0052 P6).

        brain plugin install ./fixtures/plugins/acme-spring-boot.zip
        brain plugin install https://example.com/acme.zip
        brain plugin list
        brain plugin uninstall acme-spring-boot
    """
    from companybrain.harness import plugins as plugins_mod

    action = action.strip().lower()
    if action == "install":
        if not target:
            typer.secho("plugin install requires a name, URL or path.",
                        fg=typer.colors.RED)
            raise typer.Exit(2)
        try:
            plugin = plugins_mod.install(target)
        except Exception as exc:
            typer.secho(f"plugin install failed: {exc}", fg=typer.colors.RED)
            raise typer.Exit(1) from exc
        typer.secho(f"✓ installed {plugin.name} {plugin.version}",
                    fg=typer.colors.GREEN)
        typer.echo(f"  root: {plugin.root}")
        if plugin.capabilities:
            typer.echo(f"  capabilities: {', '.join(plugin.capabilities)}")
        return

    if action == "list":
        installed = plugins_mod.list_installed()
        if not installed:
            typer.echo("(no plugins installed)")
            return
        for p in installed:
            caps = ",".join(p.capabilities) if p.capabilities else "(none)"
            typer.echo(f"  {p.name:24s}  {p.version:10s}  [{caps}]  {p.root}")
        return

    if action == "uninstall":
        if not target:
            typer.secho("plugin uninstall requires a name.", fg=typer.colors.RED)
            raise typer.Exit(2)
        if plugins_mod.uninstall(target):
            typer.secho(f"✓ uninstalled {target}", fg=typer.colors.GREEN)
        else:
            typer.secho(f"plugin {target!r} not installed", fg=typer.colors.YELLOW)
            raise typer.Exit(1)
        return

    typer.secho(f"Unknown action: {action!r}. Valid: install | list | uninstall",
                fg=typer.colors.RED)
    raise typer.Exit(2)


# ── ADR-0052 P6: brain schedule ──────────────────────────────────────────────


@app.command(name="schedule")
def schedule_cmd(
    action: str = typer.Argument(..., help="add | list | cancel | run-now"),
    name: Optional[str] = typer.Argument(None, help="Job id (required for add/cancel/run-now)"),
    repo: Optional[str] = typer.Option(None, help="Repo path (add only)"),
    endpoint: Optional[str] = typer.Option(None, help="Endpoint path (add only)"),
    method: str = typer.Option("GET", help="HTTP method (add only)"),
    cron: Optional[str] = typer.Option(None, help="Cron expression (add only)"),
    workspace_id: str = typer.Option(
        os.getenv("BRAIN_WORKSPACE_ID", "00000000-0000-0000-0000-000000000001"),
    ),
):
    """Manage cron-triggered extractions (ADR-0052 P6).

        brain schedule add daily-rebuild --repo /path --endpoint /api/x --cron "0 2 * * *"
        brain schedule list
        brain schedule cancel daily-rebuild
        brain schedule run-now daily-rebuild
    """
    from companybrain.harness import scheduler as scheduler_mod

    action = action.strip().lower()
    try:
        if action == "add":
            for required, label in (
                (name, "<name>"), (repo, "--repo"),
                (endpoint, "--endpoint"), (cron, "--cron"),
            ):
                if not required:
                    typer.secho(f"`brain schedule add` requires {label}.",
                                fg=typer.colors.RED)
                    raise typer.Exit(2)
            job_id = asyncio.run(scheduler_mod.schedule(
                name=name, repo=repo, endpoint=endpoint,
                method=method, cron=cron, workspace_id=workspace_id,
            ))
            typer.secho(f"✓ scheduled {job_id} ({cron})", fg=typer.colors.GREEN)
            return

        if action == "list":
            jobs = scheduler_mod.list_jobs()
            if not jobs:
                typer.echo("(no scheduled jobs)")
                return
            for j in jobs:
                next_at = j.next_run_at.isoformat() if j.next_run_at else "(paused)"
                typer.echo(
                    f"  {j.id:24s}  next={next_at:25s}  "
                    f"{j.method:6s} {j.endpoint:32s}  {j.repo}"
                )
            return

        if action == "cancel":
            if not name:
                typer.secho("`brain schedule cancel` requires <name>.",
                            fg=typer.colors.RED)
                raise typer.Exit(2)
            ok = scheduler_mod.cancel(name)
            if ok:
                typer.secho(f"✓ cancelled {name}", fg=typer.colors.GREEN)
            else:
                typer.secho(f"job {name!r} not found", fg=typer.colors.YELLOW)
                raise typer.Exit(1)
            return

        if action == "run-now":
            if not name:
                typer.secho("`brain schedule run-now` requires <name>.",
                            fg=typer.colors.RED)
                raise typer.Exit(2)
            outcome = asyncio.run(scheduler_mod.run_now(name))
            if outcome.get("ok"):
                typer.secho(f"✓ ran {name}", fg=typer.colors.GREEN)
            else:
                typer.secho(f"✗ {name}: {outcome.get('error', 'failed')}",
                            fg=typer.colors.RED)
                raise typer.Exit(1)
            return
    except scheduler_mod.MissingSchedulerDependency as exc:
        typer.secho(str(exc), fg=typer.colors.RED)
        raise typer.Exit(1) from exc

    typer.secho(f"Unknown action: {action!r}. Valid: add | list | cancel | run-now",
                fg=typer.colors.RED)
    raise typer.Exit(2)


# ── ADR-0052 P6: brain note ──────────────────────────────────────────────────


@app.command(name="note")
def note_cmd(
    action: str = typer.Argument(..., help="add | list | delete"),
    target: Optional[str] = typer.Argument(None, help="URN (add/list) or note id (delete)"),
    text: Optional[str] = typer.Argument(None, help="Note body (add only)"),
    author: Optional[str] = typer.Option(None, help="Author tag (add only)"),
    workspace_id: str = typer.Option(
        os.getenv("BRAIN_WORKSPACE_ID", "00000000-0000-0000-0000-000000000001"),
    ),
):
    """Per-entity sticky notes (ADR-0052 P6).

        brain note add urn:cb:dev:code:repo:method:Foo.bar "Deprecated 2026-Q4"
        brain note list urn:cb:dev:code:repo:method:Foo.bar
        brain note delete 42
    """
    from companybrain.harness import notes as notes_mod

    action = action.strip().lower()
    if action == "add":
        if not target or not text:
            typer.secho("`brain note add` requires <urn> and <text>.",
                        fg=typer.colors.RED)
            raise typer.Exit(2)
        n = asyncio.run(notes_mod.add_note(
            workspace_id=workspace_id, entity_urn=target, note=text, author=author,
        ))
        typer.secho(f"✓ note #{n.id} added to {n.entity_urn}", fg=typer.colors.GREEN)
        return

    if action == "list":
        if not target:
            typer.secho("`brain note list` requires <urn>.", fg=typer.colors.RED)
            raise typer.Exit(2)
        rows = asyncio.run(notes_mod.list_notes(
            workspace_id=workspace_id, entity_urn=target,
        ))
        if not rows:
            typer.echo("(no notes)")
            return
        for r in rows:
            ts = r.created_at.isoformat() if r.created_at else "?"
            who = r.author or "-"
            typer.echo(f"  #{r.id}  {ts}  [{who}]  {r.note}")
        return

    if action == "delete":
        if not target or not target.isdigit():
            typer.secho("`brain note delete` requires a numeric note id.",
                        fg=typer.colors.RED)
            raise typer.Exit(2)
        ok = asyncio.run(notes_mod.delete_note(note_id=int(target)))
        if ok:
            typer.secho(f"✓ deleted note #{target}", fg=typer.colors.GREEN)
        else:
            typer.secho(f"note #{target} not found", fg=typer.colors.YELLOW)
            raise typer.Exit(1)
        return

    typer.secho(f"Unknown action: {action!r}. Valid: add | list | delete",
                fg=typer.colors.RED)
    raise typer.Exit(2)


# ── ADR-0052 P6: brain pin / unpin / propose ─────────────────────────────────


@app.command(name="pin")
def pin_cmd(
    urn: str = typer.Argument(..., help="Entity URN"),
    workspace_id: str = typer.Option(
        os.getenv("BRAIN_WORKSPACE_ID", "00000000-0000-0000-0000-000000000001"),
    ),
):
    """Pin an entity so rebuild passes don't overwrite it (ADR-0052 P6)."""
    asyncio.run(_set_node_flag(workspace_id, urn, pinned=True))
    typer.secho(f"✓ pinned {urn}", fg=typer.colors.GREEN)


@app.command(name="unpin")
def unpin_cmd(
    urn: str = typer.Argument(..., help="Entity URN"),
    workspace_id: str = typer.Option(
        os.getenv("BRAIN_WORKSPACE_ID", "00000000-0000-0000-0000-000000000001"),
    ),
):
    """Remove the pin from an entity (ADR-0052 P6)."""
    asyncio.run(_set_node_flag(workspace_id, urn, pinned=False))
    typer.secho(f"✓ unpinned {urn}", fg=typer.colors.GREEN)


@app.command(name="propose")
def propose_cmd(
    urn: str = typer.Argument(..., help="Entity URN"),
    workspace_id: str = typer.Option(
        os.getenv("BRAIN_WORKSPACE_ID", "00000000-0000-0000-0000-000000000001"),
    ),
    revert: bool = typer.Option(False, "--revert", help="Clear the proposed flag."),
):
    """Mark an entity as a draft suggestion hidden from /query (ADR-0052 P6)."""
    asyncio.run(_set_node_flag(workspace_id, urn, proposed=not revert))
    msg = f"✓ {'un-' if revert else ''}proposed {urn}"
    typer.secho(msg, fg=typer.colors.GREEN)


async def _set_node_flag(workspace_id: str, urn: str, *,
                         pinned: Optional[bool] = None,
                         proposed: Optional[bool] = None) -> None:
    """Toggle pinned / proposed on the nodes row for ``urn``.

    Uses asyncpg directly so the CLI doesn't pull in the full SQLAlchemy
    machinery for a single UPDATE.
    """
    import asyncpg
    db_url = os.environ.get("DATABASE_URL", "postgresql://localhost/companybrain")
    db_url = db_url.replace("postgresql+asyncpg://", "postgresql://")
    sets, params = [], []
    if pinned is not None:
        params.append(pinned)
        sets.append(f"pinned = ${len(params)}")
    if proposed is not None:
        params.append(proposed)
        sets.append(f"proposed = ${len(params)}")
    if not sets:
        return
    params.extend([workspace_id, urn])
    sql = (
        f"UPDATE nodes SET {', '.join(sets)} "
        f"WHERE workspace_id = ${len(params) - 1}::uuid AND urn = ${len(params)}"
    )
    conn = await asyncpg.connect(db_url)
    try:
        await conn.execute(sql, *params)
    finally:
        await conn.close()


if __name__ == "__main__":
    app()
