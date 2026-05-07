"""
Application configuration — loaded from environment variables.

Quick-start (local dev, no API keys):
  Copy .env.example to .env and run:
  docker compose -f docker-compose.infra.yml up -d

LLM provider switch:
  LLM_PROVIDER=ollama      → local Ollama (default, no keys needed)
  LLM_PROVIDER=anthropic   → requires ANTHROPIC_API_KEY
  LLM_PROVIDER=openai      → requires OPENAI_API_KEY
  LLM_PROVIDER=groq        → requires GROQ_API_KEY (free tier, 300+ tok/s)
  LLM_PROVIDER=openrouter  → requires OPENROUTER_API_KEY (30+ free models)
"""

from __future__ import annotations

from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    env: str = "development"

    # ── LLM Provider ─────────────────────────────────────────────────────────
    # Switch the entire inference backend with one variable.
    # Valid values: ollama | anthropic | openai | groq | openrouter
    llm_provider: str = "groq"

    # Ollama (local) — used when llm_provider=ollama
    ollama_host: str = "http://localhost:11434"

    # Per-role model overrides for Ollama (optional)
    ollama_model_fast:      str = "llama3.1:8b"
    ollama_model_balanced:  str = "deepseek-coder-v2:16b"
    ollama_model_synthesis: str = "deepseek-r1:14b"
    ollama_model_reasoning: str = "deepseek-r1:14b"
    ollama_model_query:     str = "deepseek-r1:14b"

    # Anthropic — used when llm_provider=anthropic
    anthropic_api_key: str = ""
    # Defaults use haiku for cheap/fast tasks and sonnet for synthesis.
    # Override ANTHROPIC_MODEL_SYNTHESIS=claude-opus-4-7 for highest quality.
    anthropic_model_fast:      str = "claude-haiku-4-5-20251001"
    anthropic_model_balanced:  str = "claude-sonnet-4-6"
    anthropic_model_synthesis: str = "claude-sonnet-4-6"   # was opus — 5× cheaper, 90% quality
    anthropic_model_reasoning: str = "claude-sonnet-4-6"
    anthropic_model_query:     str = "claude-sonnet-4-6"
    # Set to true to use Anthropic's Batch API (async, 24hr, 50% cost reduction)
    anthropic_use_batch_api: bool = False

    # OpenAI — used when llm_provider=openai
    openai_api_key: str = ""
    openai_base_url: Optional[str] = None   # Set for Azure OpenAI or custom endpoints
    openai_model_fast:      str = "gpt-4o-mini"
    openai_model_balanced:  str = "gpt-4o"
    openai_model_synthesis: str = "gpt-4o"
    openai_model_reasoning: str = "gpt-4o"
    openai_model_query:     str = "gpt-4o"

    # Groq — used when llm_provider=groq
    # Get API key at: https://console.groq.com/keys  (no credit card required)
    #
    # Current model lineup (April 2026):
    #   openai/gpt-oss-20b              →  1,000 tok/s  — fastest, great for extraction
    #   meta-llama/llama-4-scout-17b-16e-instruct → 750 tok/s — vision + tool use
    #   openai/gpt-oss-120b             →    500 tok/s  — best reasoning on Groq
    #   qwen/qwen3-32b                  →    400 tok/s  — strong reasoning, 131K ctx
    #   llama-3.3-70b-versatile         →    280 tok/s  — proven, reliable
    #   llama-3.1-8b-instant            →    560 tok/s  — ultra-fast fallback
    groq_api_key: str = ""
    groq_model_fast:      str = "llama-3.1-8b-instant"                        # 560 tok/s, text model — high-volume extraction
    groq_model_balanced:  str = "meta-llama/llama-4-scout-17b-16e-instruct"   # 750 tok/s, tool-use, text model
    groq_model_synthesis: str = "meta-llama/llama-4-scout-17b-16e-instruct"   # 30k TPM — context synthesis
    groq_model_reasoning: str = "meta-llama/llama-4-scout-17b-16e-instruct"  # 30k TPM — gap detection (qwen3-32b only 6k TPM)
    groq_model_query:     str = "meta-llama/llama-4-scout-17b-16e-instruct"  # 30k TPM — user-facing queries

    # OpenRouter — used when llm_provider=openrouter
    # Free tier: 30+ models available, 20 req/min on free models
    # Get API key at: https://openrouter.ai/keys
    openrouter_api_key: str = ""
    openrouter_model_fast:      str = "meta-llama/llama-3.1-8b-instruct:free"
    openrouter_model_balanced:  str = "meta-llama/llama-3.3-70b-instruct:free"
    openrouter_model_synthesis: str = "deepseek/deepseek-r1:free"
    openrouter_model_reasoning: str = "deepseek/deepseek-r1:free"
    openrouter_model_query:     str = "meta-llama/llama-3.3-70b-instruct:free"

    # ── Database ─────────────────────────────────────────────────────────────
    database_url: str = (
        "postgresql+asyncpg://companybrain:companybrain@localhost:5432/companybrain"
    )

    # ── Redis ─────────────────────────────────────────────────────────────────
    redis_url: str = "redis://localhost:6379/0"

    # ── Git / GitHub ─────────────────────────────────────────────────────────
    github_token: str = ""   # Optional — only for PR enrichment

    # ── AWS / SQS ─────────────────────────────────────────────────────────────
    aws_region: str = "us-east-1"
    aws_access_key_id: str = "test"
    aws_secret_access_key: str = "test"
    sqs_endpoint: str = "http://localhost:4566"    # LocalStack default
    sqs_ingestion_queue: str = "company-brain-ingestion"

    # ── JWT ───────────────────────────────────────────────────────────────────
    jwt_secret: str = "dev-secret-change-in-production"

    # ── CORS ──────────────────────────────────────────────────────────────────
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://localhost:3000",
        "vscode-webview://*",
    ]

    # ── Retrieval stack ────────────────────────────────────────────────────
    qdrant_url:        str  = "http://localhost:6333"
    qdrant_api_key:    str  = ""                    # empty = no auth (dev)
    qdrant_collection: str  = "code_embeddings"
    voyage_api_key:    str  = ""                    # VOYAGE_API_KEY env var
    reranker_model:    str  = "BAAI/bge-reranker-v2-m3"  # local cross-encoder
    bm25_top_k:        int  = 50                    # BM25 candidates before rerank
    dense_top_k:       int  = 50                    # dense vector candidates
    rerank_top_k:      int  = 10                    # final reranked results
    hybrid_search_enabled: bool = True             # feature flag

    # ── Observability ──────────────────────────────────────────────────────
    langfuse_public_key:  str  = ""                # LANGFUSE_PUBLIC_KEY env var
    langfuse_secret_key:  str  = ""                # LANGFUSE_SECRET_KEY env var
    langfuse_host:        str  = "http://localhost:3001"
    otel_endpoint:        str  = ""                # e.g. http://localhost:4318/v1/traces
    otel_enabled:         bool = False

    # ── Pipeline tuning ───────────────────────────────────────────────────────
    max_commits_per_repo: int = 200
    cluster_window_hours: int = 24
    max_entity_extraction_concurrency: int = 10
    max_context_synthesis_concurrency: int = 5

    # ── Context window & output token budgets ────────────────────────────────
    # max_input_tokens: hard cap on prompt size sent to any LLM.
    #   Claude models support 200K tokens; gpt-4o supports 128K.
    #   Keep headroom for the response: set to (context_window - max_output_tokens).
    #   Default 120_000 is safe for both providers.
    max_input_tokens: int = 120_000

    # Per-stage output token limits.  Raise these if you see truncated JSON responses.
    # The previous hardcoded value for entity extraction was 512 — dangerously low
    # for files with many classes/methods.
    # Groq llama-3.1-8b-instant supports up to 8K output tokens.
    # Diff-based extraction can produce large JSON arrays — 6K gives ample headroom.
    max_tokens_entity_extraction:  int = 6_000   # Stage 1  — raised from 2048
    max_tokens_intent_synthesis:   int = 2_048   # Stage 1.5
    max_tokens_relationship:       int = 4_096   # Stage 2
    max_tokens_context_synthesis:  int = 2_048   # Stage 3
    max_tokens_gap_detection:      int = 4_096   # Stage 4
    max_tokens_query:              int = 4_096   # Live query responses


settings = Settings()
