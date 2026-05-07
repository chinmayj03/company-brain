"""
CodeEmbedder — code embeddings with tiered provider selection.

Provider priority (first available wins):
  1. voyage-code-3      (VOYAGE_API_KEY set)       — best quality, paid
  2. FastEmbed local    (qdrant-client[fastembed])  — free, CPU, code-specific
     model: jinaai/jina-embeddings-v2-base-code (768 dims)
  3. Ollama             (OLLAMA_HOST reachable)     — free, GPU-optional
     model: nomic-embed-text or nomic-embed-code
  4. Disabled           (none of the above)         → BM25-only mode

All providers share the same Redis content-addressed cache
(cache key includes model name, so switching providers never
returns stale vectors from a different model's run).

Usage::
    embedder = CodeEmbedder()
    vectors = await embedder.embed_batch(["def foo():", "class Bar:"])
    # vectors: list[list[float]] | None  (None if all providers unavailable)
"""
from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Optional
import structlog

log = structlog.get_logger(__name__)

# ── Provider-specific model names ─────────────────────────────────────────────
_VOYAGE_MODEL        = "voyage-code-3"           # dims = 1024
_FASTEMBED_MODEL     = "jinaai/jina-embeddings-v2-base-code"  # dims = 768, code-specific
_OLLAMA_EMBED_MODEL  = "nomic-embed-text"        # dims = 768; use nomic-embed-code if pulled

CACHE_TTL = 30 * 24 * 3600   # 30 days


class CodeEmbedder:
    """
    Embed code snippets using the best available provider.
    Automatically falls through the tier list at first use.
    All vectors are cached in Redis by content hash.
    """

    def __init__(self, api_key: str = "", redis_url: str = ""):
        from companybrain.config import settings
        self._voyage_key = api_key or settings.voyage_api_key
        self._redis_url  = redis_url or getattr(settings, "redis_url", "redis://localhost:6379")
        self._ollama_host = getattr(settings, "ollama_host", "http://localhost:11434")

        self._vo_client       = None
        self._fastembed_model = None
        self._redis           = None

        # Will be resolved on first embed call
        self._provider: Optional[str] = None   # "voyage" | "fastembed" | "ollama" | None

    # ── Provider resolution ────────────────────────────────────────────────────

    async def _resolve_provider(self) -> Optional[str]:
        """Pick the best available provider. Called once, result cached."""
        if self._provider is not None:
            return self._provider

        # Tier 1 — Voyage (paid, best quality)
        if self._voyage_key:
            try:
                import voyageai  # type: ignore
                self._vo_client = voyageai.AsyncClient(api_key=self._voyage_key)
                self._provider = "voyage"
                log.info("Embedding provider: voyage-code-3 (API)")
                return self._provider
            except ImportError:
                log.debug("voyageai not installed, trying next provider")

        # Tier 2 — FastEmbed local (free, code-specific, no API key)
        try:
            from fastembed import TextEmbedding  # type: ignore
            self._fastembed_model = TextEmbedding(model_name=_FASTEMBED_MODEL)
            self._provider = "fastembed"
            log.info("Embedding provider: FastEmbed local", model=_FASTEMBED_MODEL)
            return self._provider
        except ImportError:
            log.debug("fastembed not installed, trying Ollama")
        except Exception as e:
            log.debug("FastEmbed init failed", error=str(e))

        # Tier 3 — Ollama (free, requires Ollama running)
        try:
            import httpx
            resp = await asyncio.wait_for(
                asyncio.get_event_loop().run_in_executor(
                    None,
                    lambda: httpx.get(f"{self._ollama_host}/api/tags", timeout=2)
                ),
                timeout=3,
            )
            if resp.status_code == 200:
                self._provider = "ollama"
                log.info("Embedding provider: Ollama", model=_OLLAMA_EMBED_MODEL,
                         hint="run 'ollama pull nomic-embed-text' if not already pulled")
                return self._provider
        except Exception:
            pass

        # No provider available
        self._provider = ""   # empty string = disabled, don't retry
        log.warning(
            "No embedding provider available — using BM25-only retrieval.\n"
            "  Free options:\n"
            "    • pip install 'qdrant-client[fastembed]'  (auto-downloads model, no key needed)\n"
            "    • ollama pull nomic-embed-text            (requires Ollama running)\n"
            "  Paid option:\n"
            "    • Set VOYAGE_API_KEY in .env             (voyage-code-3, best quality)"
        )
        return None

    @property
    def enabled(self) -> bool:
        # Unknown until first resolve; return optimistic True so callers proceed
        if self._provider is None:
            return True
        return bool(self._provider)

    # ── Redis cache ────────────────────────────────────────────────────────────

    def _get_redis(self):
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
            except Exception:
                pass
        return self._redis

    # ── Public API ─────────────────────────────────────────────────────────────

    async def embed_batch(self, texts: list[str]) -> Optional[list[list[float]]]:
        """
        Embed a batch of code snippets. Returns list of float vectors or None.
        Cache misses are fetched from the active provider and stored in Redis.
        """
        if not texts:
            return None

        provider = await self._resolve_provider()
        if not provider:
            return None

        model_tag = {
            "voyage":    _VOYAGE_MODEL,
            "fastembed": _FASTEMBED_MODEL,
            "ollama":    _OLLAMA_EMBED_MODEL,
        }.get(provider, provider)

        keys = [_cache_key(t, model_tag) for t in texts]
        vectors: list[Optional[list[float]]] = [None] * len(texts)
        miss_indices: list[int] = []

        # Check Redis cache
        r = self._get_redis()
        if r is not None:
            try:
                cached = await r.mget(*[f"cb:emb:{k}" for k in keys])
                for i, val in enumerate(cached):
                    if val is not None:
                        vectors[i] = json.loads(val)
                    else:
                        miss_indices.append(i)
            except Exception:
                miss_indices = list(range(len(texts)))
        else:
            miss_indices = list(range(len(texts)))

        if not miss_indices:
            log.debug("Embedding cache: 100% hit", count=len(texts))
            return [v for v in vectors if v is not None]

        miss_texts = [texts[i] for i in miss_indices]
        new_vectors = await self._embed_with_provider(provider, miss_texts, input_type="document")
        if new_vectors is None:
            return None

        # Populate vectors + write back to cache
        if r is not None:
            try:
                pipe = r.pipeline()
                for i, idx in enumerate(miss_indices):
                    vectors[idx] = new_vectors[i]
                    pipe.setex(f"cb:emb:{keys[idx]}", CACHE_TTL, json.dumps(new_vectors[i]))
                await pipe.execute()
            except Exception:
                for i, idx in enumerate(miss_indices):
                    vectors[idx] = new_vectors[i]
        else:
            for i, idx in enumerate(miss_indices):
                vectors[idx] = new_vectors[i]

        hit_rate = (len(texts) - len(miss_indices)) / len(texts)
        log.debug("Embedding complete", total=len(texts),
                  misses=len(miss_indices), hit_rate=f"{hit_rate:.0%}",
                  provider=provider)
        return [v for v in vectors if v is not None]

    async def embed_query(self, query: str) -> Optional[list[float]]:
        """Embed a single query string for retrieval."""
        provider = await self._resolve_provider()
        if not provider:
            return None
        result = await self._embed_with_provider(provider, [query], input_type="query")
        return result[0] if result else None

    # ── Provider dispatch ──────────────────────────────────────────────────────

    async def _embed_with_provider(
        self,
        provider: str,
        texts: list[str],
        input_type: str = "document",
    ) -> Optional[list[list[float]]]:
        try:
            if provider == "voyage":
                return await self._embed_voyage(texts, input_type)
            elif provider == "fastembed":
                return await self._embed_fastembed(texts)
            elif provider == "ollama":
                return await self._embed_ollama(texts)
        except Exception as e:
            log.warning("Embedding provider failed", provider=provider, error=str(e))
        return None

    async def _embed_voyage(self, texts: list[str], input_type: str) -> list[list[float]]:
        result = await self._vo_client.embed(texts, model=_VOYAGE_MODEL, input_type=input_type)
        return result.embeddings

    async def _embed_fastembed(self, texts: list[str]) -> list[list[float]]:
        # FastEmbed is sync — run in executor to avoid blocking the event loop
        loop = asyncio.get_event_loop()
        def _sync_embed():
            return list(self._fastembed_model.embed(texts))
        raw = await loop.run_in_executor(None, _sync_embed)
        # fastembed returns numpy arrays; convert to plain float lists
        return [vec.tolist() if hasattr(vec, "tolist") else list(vec) for vec in raw]

    async def _embed_ollama(self, texts: list[str]) -> list[list[float]]:
        import httpx
        vectors = []
        async with httpx.AsyncClient(timeout=30) as client:
            for text in texts:
                resp = await client.post(
                    f"{self._ollama_host}/api/embeddings",
                    json={"model": _OLLAMA_EMBED_MODEL, "prompt": text},
                )
                resp.raise_for_status()
                vectors.append(resp.json()["embedding"])
        return vectors


def _cache_key(text: str, model: str) -> str:
    return hashlib.sha256(f"{model}:v1:{text}".encode()).hexdigest()[:32]
