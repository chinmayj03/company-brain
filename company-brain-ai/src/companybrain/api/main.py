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

from companybrain.api.routes import pipeline, query, health, repo, feedback, stream, conversations, mcp_agents, sources, suggestions
from companybrain.config import settings
from companybrain.db import init_db_pool, close_db_pool

log = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown lifecycle."""
    log.info("Starting Company Brain AI service", version="0.1.0")
    await init_db_pool()
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
# ADR-0051 P4 — SSE feed of harness TodoList progress for /pipeline/jobs/{id}.
# Path is fully embedded in the route so no prefix is set here.
app.include_router(stream.router, tags=["stream"])
# ADR-0072: MCP telemetry, source registry, suggested questions
app.include_router(mcp_agents.router, prefix="/mcp", tags=["mcp-agents"])
app.include_router(sources.router, tags=["sources"])
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
