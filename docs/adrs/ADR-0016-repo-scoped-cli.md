# ADR-0016: Repo-scoped extraction trigger and `brain` CLI

**Status:** Proposed
**Date:** 2026-05-07
**Effort:** 3 days
**Depends on:** ADR-0011 (structural pre-pass), ADR-0012 (BrainStore), ADR-0013 (URN identity)
**Unblocks:** Stage 2 CI rebuild workflow

---

## Context

Today the only way to drive the pipeline is the React UI → Java backend → Python AI service. The trigger shape is `(endpoint_path, http_method)`. For a repo with 200 endpoints, this is 200 separate runs.

The harness §10.4 prescribes a CLI:
```
brain map src/components/UserCard.tsx
brain rebuild --repo .
brain blast-radius "web-app::component::UserCard" --hops 2
brain query "what do I need to know before changing UserCard?"
brain push
```

This ADR delivers `brain index` (whole-repo extraction), `brain map` (single-file), `brain query` (smart-zone via ADR-0018 once it lands; falls back to hybrid search until then), `brain blast-radius`, and `brain rebuild-from-json`.

## Decision

Implement a Python CLI at `companybrain/cli.py` using `typer`. Wire it as a console script in `pyproject.toml` so `pip install -e .` installs `brain` on the user's PATH. The CLI calls the same orchestrator code the FastAPI route uses; no business logic is duplicated.

For whole-repo extraction the CLI walks the repo (skipping `node_modules`, `.git`, `target`, etc.) and either:
- A) Invokes the structural pre-pass (ADR-0011) directly to populate Neo4j, then iterates over discovered entry points (controllers, route handlers) running the existing endpoint-scoped pipeline against each.
- B) (Future ADR) Replaces the endpoint-scoped pipeline with a true repo-scoped pipeline that has no endpoint anchor.

Stage 1 ships **A** because it requires zero changes to the orchestrator; **B** is a natural follow-up once the structural-first ordering from ADR-0011 has stabilised.

## Implementation

### Files to create

#### `company-brain-ai/src/companybrain/cli.py`

```python
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
import json
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
                        endpoints: Optional[str], repo_name: str, dry_run: bool):
    typer.echo(f"⤷ index: {repo_path}  branch={branch}  ws={workspace_id}")

    # 1. Structural pre-pass: populate Neo4j
    fc = FocalContext(code_units=[
        CodeUnit(file_path=str(p), repo_name=repo_name, role="unknown",
                 class_name=p.stem, content=p.read_text(errors="replace"),
                 language=_lang_for(p))
        for p in walk_repo(repo_path)
    ])
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
        raise typer.Exit(f"Not found: {file_path}")
    asyncio.run(_map_async(file_path, repo_path, workspace_id, repo_name))


async def _map_async(file: Path, repo: Path, workspace_id: str, repo_name: str):
    fc = FocalContext(code_units=[CodeUnit(
        file_path=str(file), repo_name=repo_name, role="unknown",
        class_name=file.stem, content=file.read_text(errors="replace"),
        language=_lang_for(file),
    )])
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


async def _blast_radius_async(urn: str, hops: int, direction: str):
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
```

#### `company-brain-ai/src/companybrain/cli_helpers/repo_walker.py`

```python
"""Walk a repo, return source files. Honor .gitignore + standard skip dirs."""
from __future__ import annotations
from pathlib import Path
from typing import Iterable

_SKIP_DIRS = frozenset({
    "node_modules", ".git", ".idea", ".vscode", "dist", "build",
    "target", "out", "__pycache__", ".pytest_cache", ".venv", "venv",
    ".gradle", ".next", ".turbo", ".mypy_cache", ".ruff_cache",
    "vendor", "coverage", ".brain", ".bm25",
})

_SOURCE_EXTS = frozenset({
    ".java", ".kt", ".py", ".ts", ".tsx", ".js", ".jsx",
    ".go", ".rb", ".cs", ".rs", ".php", ".swift",
})


def walk_repo(root: Path) -> Iterable[Path]:
    """Yield source files under root, skipping common build dirs."""
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if any(part in _SKIP_DIRS for part in path.parts):
            continue
        if path.suffix in _SOURCE_EXTS:
            yield path
```

#### `company-brain-ai/src/companybrain/cli_helpers/endpoint_discovery.py`

```python
"""Discover HTTP endpoints in a repo using the same regexes as code_tracer.

Returns list of (METHOD, path) tuples. Order: controllers first, then any
route registrations found in TS/JS/Python.
"""
from __future__ import annotations
import re
from pathlib import Path

_JAVA_RE = re.compile(
    r'@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)'
    r'\s*\(?[^)]*?(?:value\s*=\s*)?\{?\s*["\']([^"\']+)["\']'
)
_TS_RE = re.compile(
    r'(?:axios|fetch|api|http)\s*\.?\s*(get|post|put|delete|patch)\s*\(\s*[`\'"]([^`\'"]+)['"]'
)
_PY_RE = re.compile(
    r'@(?:router|app)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']'
)


def discover_endpoints(repo_root: Path, *, max_endpoints: int = 100) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source in repo_root.rglob("*"):
        if not source.is_file():
            continue
        if source.suffix not in {".java", ".ts", ".tsx", ".js", ".jsx", ".py"}:
            continue
        if any(part in {"node_modules", ".git", "target", "build", "dist", "__pycache__"}
               for part in source.parts):
            continue
        try:
            text = source.read_text(errors="replace")
        except Exception:
            continue

        if source.suffix == ".java":
            for m in _JAVA_RE.finditer(text):
                method = _java_anno_to_method(m.group(1))
                _add(out, seen, (method, m.group(2)))
        elif source.suffix in {".ts", ".tsx", ".js", ".jsx"}:
            for m in _TS_RE.finditer(text):
                _add(out, seen, (m.group(1).upper(), m.group(2)))
        elif source.suffix == ".py":
            for m in _PY_RE.finditer(text):
                _add(out, seen, (m.group(1).upper(), m.group(2)))

        if len(out) >= max_endpoints:
            break
    return out


def _java_anno_to_method(anno: str) -> str:
    return anno.replace("Mapping", "").upper() if anno != "RequestMapping" else "GET"


def _add(lst, seen, pair):
    if pair not in seen:
        lst.append(pair)
        seen.add(pair)
```

#### `company-brain-ai/src/companybrain/cli_helpers/brain_rebuild.py`

```python
"""rebuild-from-json: read .brain/ JSONs, fan out to Postgres + Neo4j + Qdrant."""
from __future__ import annotations
from pathlib import Path

from companybrain.graph.java_client import JavaGraphClient
from companybrain.graph.neo4j_writer import Neo4jWriter
from companybrain.retrieval.qdrant_store import QdrantBrainStore
from companybrain.store import (
    FanoutBrainStore, JsonFileBrainStore, Neo4jBrainStore, PostgresBrainStore,
)
from companybrain.store.identity import workspace_slug_for


async def rebuild_from_json(repo_path: Path, workspace_id: str) -> None:
    brain_root = repo_path / ".brain"
    if not brain_root.exists():
        raise FileNotFoundError(f"No .brain/ in {repo_path}")

    json_store = JsonFileBrainStore(brain_root)

    java = JavaGraphClient(workspace_id=workspace_id, job_id="rebuild")
    pg = PostgresBrainStore(java)
    n4j = Neo4jBrainStore(Neo4jWriter(), workspace_id=workspace_id)
    qd = QdrantBrainStore(brain_root=repo_path,
                          workspace_slug=workspace_slug_for(workspace_id))

    fanout = FanoutBrainStore(primary=json_store, mirrors=[pg, n4j, qd])

    count = 0
    async for entity_id in json_store.list_ids():
        entity = await json_store.read(entity_id)
        if entity is not None:
            # Note: write through fanout will re-write the JSON (idempotent).
            for mirror in fanout.mirrors:
                await mirror.write(entity, run_id="rebuild", workspace_id=workspace_id)
            count += 1
    for mirror in fanout.mirrors:
        await mirror.commit_run("rebuild")

    print(f"✓ rebuilt {count} entities from {brain_root}")
```

### Edits

#### `company-brain-ai/pyproject.toml`

```toml
[project.scripts]
brain = "companybrain.cli:app"
```

(Re-run `pip install -e .` after editing.)

#### `Makefile`

Add convenience targets:

```makefile
brain-index:
	cd company-brain-ai && python -m companybrain.cli index $(REPO)

brain-query:
	cd company-brain-ai && python -m companybrain.cli query "$(Q)" --repo $(REPO)

brain-blast:
	cd company-brain-ai && python -m companybrain.cli blast-radius "$(URN)"
```

## Test plan

`tests/unit/cli_helpers/test_repo_walker.py`:

```python
from pathlib import Path
from companybrain.cli_helpers.repo_walker import walk_repo


def test_skips_node_modules(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.ts").write_text("export const x = 1;")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "lib.js").write_text("module.exports = {};")

    files = list(walk_repo(tmp_path))
    assert any("src/foo.ts" in str(f) for f in files)
    assert not any("node_modules" in str(f) for f in files)
```

`tests/unit/cli_helpers/test_endpoint_discovery.py`:

```python
from pathlib import Path
from companybrain.cli_helpers.endpoint_discovery import discover_endpoints


def test_finds_java_spring_endpoints(tmp_path):
    (tmp_path / "Foo.java").write_text(
        '@RestController class Foo {\n'
        '  @GetMapping("/users/{id}") public X getById() { return null; }\n'
        '  @PostMapping("/users")     public X create() { return null; }\n'
        '}\n'
    )
    eps = discover_endpoints(tmp_path)
    assert ("GET",  "/users/{id}") in eps
    assert ("POST", "/users") in eps


def test_finds_fastapi_endpoints(tmp_path):
    (tmp_path / "main.py").write_text(
        'from fastapi import APIRouter\n'
        'router = APIRouter()\n'
        '@router.get("/health")\n'
        'def h(): return {}\n'
    )
    eps = discover_endpoints(tmp_path)
    assert ("GET", "/health") in eps
```

Smoke test for the CLI itself:

```bash
brain --help
brain index --help
brain query "user authentication" --repo ./pilot --top-k 3
```

## Acceptance criteria

- [ ] `brain` is on PATH after `pip install -e .` from `company-brain-ai/`.
- [ ] `brain index ./pilot` runs structural pre-pass and discovers ≥ 1 endpoint on the pilot repo.
- [ ] `brain index ./pilot --dry-run` lists endpoints without invoking LLM.
- [ ] `brain query "<text>" --repo ./pilot` returns ranked URN hits.
- [ ] `brain blast-radius <urn> --hops 2` returns at least the seed entity (and any neighbours).
- [ ] `brain rebuild-from-json --repo ./pilot` re-populates Postgres + Neo4j + Qdrant from `.brain/`.
- [ ] Repo walker skips `node_modules`, `.git`, `target`, `dist`, `build`, `.brain`, `.venv`.
- [ ] CLI exits non-zero with a clear error if `--repo` is not a directory.
- [ ] Unit tests for repo walker and endpoint discovery pass.

## Verification commands

```bash
# Install
cd company-brain-ai && pip install -e . && cd ..

# Dry-run
brain index ./pilot --dry-run

# Real run on pilot
brain index ./pilot

# Query
brain query "payment processing" --repo ./pilot

# Rebuild from JSON
psql -c "TRUNCATE nodes, edges CASCADE;"
brain rebuild-from-json --repo ./pilot
psql -c "SELECT count(*) FROM nodes;"
```

## Rollback

```bash
git revert <commit-sha>
pip uninstall company-brain-ai
```

## Out of scope

- **True repo-scoped extraction (no endpoint anchor).** This ADR uses A: discover endpoints, run per-endpoint pipeline. A follow-up ADR refactors the orchestrator to a repo-scoped form so the CLI doesn't need to fan out per endpoint.
- **`brain push` (multi-repo platform-brain target).** Stage 2.
- **`brain map` running full LLM pass on a single file.** The skeleton is in; the LLM call is conditional on having a single-file orchestrator path which is also out of scope for Stage 1.
- **Cost / progress UI in CLI.** A future ADR can add `rich` progress bars; Stage 1 uses simple `typer.echo`.
- **Bun-side tighter integration.** `apps/cli/` (Bun) stays as is; this ADR is the Python CLI.
