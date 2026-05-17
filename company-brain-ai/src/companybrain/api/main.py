"""
Company Brain AI Service — FastAPI entry point.

Exposes:
  POST /pipeline/start       — trigger context builder pipeline for an API endpoint
  GET  /pipeline/jobs/{id}   — poll pipeline job status
  POST /query                — natural language query over the dependency graph
  POST /ingest/process       — internal: process an ingestion event batch from SQS worker
"""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from companybrain.api.routes import pipeline, query, health, repo, feedback, stream, conversations, mcp_agents, suggestions
from companybrain.api.routes import me as me_route, workspace as workspace_route
from companybrain.api.routes import repos as repos_route, owners as owners_route
from companybrain.api.routes import brain as brain_route
from companybrain.config import settings
from companybrain.db import init_db_pool, close_db_pool

log = structlog.get_logger(__name__)


async def _ensure_dev_workspace() -> None:
    """Upsert the well-known dev workspace row so conversations FK is satisfied."""
    from companybrain.db import get_session
    from sqlalchemy import text
    dev_id = "00000000-0000-0000-0000-000000000001"
    try:
        async with get_session() as session:
            await session.execute(text("""
                INSERT INTO workspaces (id, name, slug)
                VALUES (:id, 'Development', 'dev')
                ON CONFLICT (id) DO NOTHING
            """), {"id": dev_id})
            await session.commit()
        log.info("Dev workspace ensured", workspace_id=dev_id)
    except Exception as exc:
        log.warning("Could not upsert dev workspace (non-fatal)", error=str(exc))


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    log.info("Starting Company Brain AI service", version="0.1.0")
    await init_db_pool()
    await _ensure_dev_workspace()
    yield
    await close_db_pool()
    log.info("Company Brain AI service stopped")


app = FastAPI(
    title="Company Brain AI",
    description="LLM extraction pipeline and dependency graph query engine",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.env != "production" else None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, tags=["health"])
app.include_router(pipeline.router, prefix="/pipeline", tags=["pipeline"])
app.include_router(query.router, prefix="/query", tags=["query"])
app.include_router(repo.router, prefix="/repo", tags=["repo"])
app.include_router(feedback.router, prefix="/feedback", tags=["feedback"])
# ADR-0072 A1+A2+A5 — Conversation history, saved queries, audit log.
app.include_router(conversations.router, prefix="/conversations", tags=["conversations"])
# ADR-0071 — brain browser (read-only .brain/ projection for demo UI)
app.include_router(brain_route.router, prefix="/brain", tags=["brain"])
# ADR-0073 — frontend live-up routes
app.include_router(me_route.router, tags=["me"])
app.include_router(workspace_route.router, prefix="/workspaces", tags=["workspaces"])
app.include_router(repos_route.router, prefix="/workspaces", tags=["repos"])
app.include_router(owners_route.router, prefix="/entities", tags=["owners"])
# ADR-0051 P4 — SSE feed of harness TodoList progress for /pipeline/jobs/{id}.
# Path is fully embedded in the route so no prefix is set here.
app.include_router(stream.router, tags=["stream"])
# ADR-0072: MCP telemetry + suggested questions
# Source registry moved to Java backend (SourceController /v1/workspaces/.../sources)
app.include_router(mcp_agents.router, prefix="/mcp", tags=["mcp-agents"])
app.include_router(suggestions.router, tags=["suggestions"])

# ── ADR-0052 P5: brain-as-MCP route ──────────────────────────────────────
# Exposes the harness MCP surface (query_brain, read_entity, find_callers, ...)
# under /mcp/harness so external IDE/agent clients can connect to the same
# FastAPI service that backs the pipeline. Routes are mounted lazily — the
# handlers spin up a per-request server bound to the requested workspace.
if settings.harness_mcp_enabled:
    from companybrain.api.routes import harness_mcp  # noqa: E402
    app.include_router(harness_mcp.router, prefix="/mcp/harness", tags=["mcp"])


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error("Unhandled exception", path=request.url.path, error=str(exc), exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
