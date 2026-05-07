"""
Embedding providers for the Company Brain retrieval stack.

Two APIs are provided:
  make_embedder() / Embedder  — ADR-0015: synchronous Protocol-based API.
                                 Used by HybridSearcher and QdrantBrainStore.
  CodeEmbedder                — Legacy async API with Redis caching (backward compat).
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from typing import Optional, Protocol, runtime_checkable

import structlog

log = structlog.get_logger(__name__)


# ── ADR-0015: Embedder protocol + sync concrete implementations ───────────────

@runtime_checkable
class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]: ...
    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


def make_embedder() -> Embedder:
    """Resolve the best available embedder at startup.

    Order:
      1. Voyage AI (voyage-code-3)       — if VOYAGE_API_KEY is set.
      2. OpenAI text-embedding-3-small   — if OPENAI_API_KEY is set.
      3. sentence-transformers all-MiniLM-L6-v2 — local fallback.

    Override with BRAIN_EMBEDDER=local to force the local fallback.
    """
    if os.getenv("BRAIN_EMBEDDER") == "local":
        return _try_minilm()
    if os.getenv("VOYAGE_API_KEY"):
        return _VoyageCode3()
    if os.getenv("OPENAI_API_KEY"):
        return _OpenAITextSmall()
    return _try_minilm()


def _try_minilm() -> Embedder:
    try:
        return _LocalMiniLM()
    except ImportError:
        log.warning(
            "sentence_transformers not installed — using hash embedder. "
            "pip install sentence-transformers for semantic quality."
        )
        return _HashEmbedder()


class _VoyageCode3:
    dim = 1024

    def __init__(self) -> None:
        import voyageai  # type: ignore
        self._client = voyageai.Client()

    def embed(self, text: str) -> list[float]:
        r = self._client.embed([text], model="voyage-code-3", input_type="document")
        return r.embeddings[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        r = self._client.embed(texts, model="voyage-code-3", input_type="document")
        return r.embeddings


class _OpenAITextSmall:
    dim = 1536

    def __init__(self) -> None:
        from openai import OpenAI  # type: ignore
        self._client = OpenAI()

    def embed(self, text: str) -> list[float]:
        r = self._client.embeddings.create(input=[text], model="text-embedding-3-small")
        return r.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        r = self._client.embeddings.create(input=texts, model="text-embedding-3-small")
        return [d.embedding for d in r.data]


class _LocalMiniLM:
    dim = 384

    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer  # type: ignore
        self._m = SentenceTransformer("all-MiniLM-L6-v2")
        log.info("Embedder: local all-MiniLM-L6-v2 (dim=384)")

    def embed(self, text: str) -> list[float]:
        return self._m.encode(text, normalize_embeddings=True).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return self._m.encode(texts, normalize_embeddings=True, batch_size=32).tolist()


class _HashEmbedder:
    """Deterministic hash-based embedder — no external deps.

    Used when sentence_transformers is unavailable (e.g. lightweight CI).
    Vectors are reproducible but not semantically meaningful.
    """
    dim = 384

    def embed(self, text: str) -> list[float]:
        import hashlib
        import math
        seed = int(hashlib.md5(text.encode()).hexdigest(), 16)
        result = []
        for i in range(self.dim):
            seed = (seed * 1664525 + 1013904223) & 0xFFFFFFFF
            val = math.sin(seed + i) * 0.5 + 0.5
            result.append(val - 0.5)
        norm = math.sqrt(sum(v * v for v in result)) or 1.0
        return [v / norm for v in result]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


# ── Legacy: CodeEmbedder — async with Redis caching ───────────────────────────

_VOYAGE_MODEL        = "voyage-code-3"
_FASTEMBED_MODEL     = "jinaai/jina-embeddings-v2-base-code"
_OLLAMA_EMBED_MODEL  = "nomic-embed-text"
CACHE_TTL = 30 * 24 * 3600   # 30 days


class CodeEmbedder:
    """Async code embedder with tiered provider selection and Redis caching."""

    def __init__(self, api_key: str = "", redis_url: str = ""):
        from companybrain.config import settings
        self._voyage_key = api_key or settings.voyage_api_key
        self._redis_url  = redis_url or getattr(settings, "redis_url", "redis://localhost:6379")
        self._ollama_host = getattr(settings, "ollama_host", "http://localhost:11434")
        self._vo_client       = None
        self._fastembed_model = None
        self._redis           = None
        self._provider: Optional[str] = None

    async def _resolve_provider(self) -> Optional[str]:
        if self._provider is not None:
            return self._provider
        if self._voyage_key:
            try:
                import voyageai  # type: ignore
                self._vo_client = voyageai.AsyncClient(api_key=self._voyage_key)
                self._provider = "voyage"
                log.info("Embedding provider: voyage-code-3 (API)")
                return self._provider
            except ImportError:
                log.debug("voyageai not installed, trying next provider")
        try:
            from fastembed import TextEmbedding  # type: ignore
            self._fastembed_model = TextEmbedding(model_name=_FASTEMBED_MODEL)
            self._provider = "fastembed"
            log.info("Embedding provider: FastEmbed local", model=_FASTEMBED_MODEL)
            return self._provider
        except (ImportError, Exception) as e:
            log.debug("FastEmbed unavailable", error=str(e))
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
                log.info("Embedding provider: Ollama", model=_OLLAMA_EMBED_MODEL)
                return self._provider
        except Exception:
            pass
        self._provider = ""
        log.warning("No embedding provider available — using BM25-only retrieval.")
        return None

    @property
    def enabled(self) -> bool:
        if self._provider is None:
            return True
        return bool(self._provider)

    def _get_redis(self):
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
                self._redis = aioredis.from_url(self._redis_url, decode_responses=True)
            except Exception:
                pass
        return self._redis

    async def embed_batch(self, texts: list[str]) -> Optional[list[list[float]]]:
        if not texts:
            return None
        provider = await self._resolve_provider()
        if not provider:
            return None
        model_tag = {"voyage": _VOYAGE_MODEL, "fastembed": _FASTEMBED_MODEL,
                     "ollama": _OLLAMA_EMBED_MODEL}.get(provider, provider)
        keys = [_cache_key(t, model_tag) for t in texts]
        vectors: list[Optional[list[float]]] = [None] * len(texts)
        miss_indices: list[int] = []
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
            return [v for v in vectors if v is not None]
        miss_texts = [texts[i] for i in miss_indices]
        new_vectors = await self._embed_with_provider(provider, miss_texts, "document")
        if new_vectors is None:
            return None
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
        return [v for v in vectors if v is not None]

    async def embed_query(self, query: str) -> Optional[list[float]]:
        provider = await self._resolve_provider()
        if not provider:
            return None
        result = await self._embed_with_provider(provider, [query], "query")
        return result[0] if result else None

    async def _embed_with_provider(self, provider: str, texts: list[str],
                                   input_type: str = "document") -> Optional[list[list[float]]]:
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
        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, lambda: list(self._fastembed_model.embed(texts)))
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
