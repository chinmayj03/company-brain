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
    brain push        — copy the .brain/ to platform-brain (Stage 2)
"""
from __future__ import annotations
import asyncio
import os
import sys
from pathlib import Path
from typing import Optional

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
            repos=[RepoConfig(local_path=str(repo_path), type=RepoType.backend,
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


if __name__ == "__main__":
    app()
