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

from companybrain.api.routes import pipeline, query, health, repo, feedback
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


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log.error("Unhandled exception", path=request.url.path, error=str(exc), exc_info=True)
    return JSONResponse(status_code=500, content={"detail": "Internal server error"})
