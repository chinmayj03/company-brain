"""
factory.py — ADR-0015 A1.2: build a RetrievalPipeline from application settings.

``make_retrieval_pipeline(settings)`` is the single public entry point.  It
wires HybridSearcher + optional Reranker with graceful degradation so callers
always get a usable pipeline even when Qdrant is down or sentence-transformers
is not installed.

Typical usage (from FastAPI startup or a per-request helper)::

    from companybrain.retrieval.factory import make_retrieval_pipeline
    from companybrain.config import settings

    pipeline = make_retrieval_pipeline(settings, brain_root=repo_path, workspace_slug=slug)
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import structlog

from companybrain.retrieval.pipeline import RetrievalPipeline

# Imported at module level so unit tests can patch these names on this module.
# Both HybridSearcher and Reranker lazy-load heavy deps (Qdrant client,
# sentence-transformers) only when first used, so the import itself is cheap.
try:
    from companybrain.retrieval.hybrid_search import HybridSearcher
    from companybrain.retrieval.embedder import make_embedder
except Exception as _hybrid_import_err:  # noqa: BLE001
    HybridSearcher = None  # type: ignore[assignment,misc]
    make_embedder = None   # type: ignore[assignment]
    log_tmp = structlog.get_logger(__name__)
    log_tmp.warning("[factory] HybridSearcher import failed at module load",
                    error=str(_hybrid_import_err))

try:
    from companybrain.retrieval.reranker import Reranker
except Exception as _reranker_import_err:  # noqa: BLE001
    Reranker = None  # type: ignore[assignment,misc]

log = structlog.get_logger(__name__)


def make_retrieval_pipeline(
    settings,
    *,
    brain_root: Optional[Path | str] = None,
    workspace_slug: str = "default",
) -> RetrievalPipeline:
    """Build a ``RetrievalPipeline`` from application settings.

    Graceful degradation policy:
    - If Qdrant is unreachable, ``HybridSearcher`` handles it internally
      (falls back to BM25-only per entity type).
    - If ``sentence-transformers`` is not installed, ``Reranker`` falls back
      to passthrough mode (original RRF scores, ``reranked=False``).
    - If any constructor raises unexpectedly, the factory logs a warning and
      returns a minimal pipeline with a stub searcher that always returns [].

    Args:
        settings:        Application ``Settings`` instance (from config.py).
        brain_root:      Path to the brain root directory (where .bm25/ indices
                         live). Defaults to "." when not supplied — the
                         HybridSearcher handles missing directories gracefully.
        workspace_slug:  Qdrant collection namespace.  Defaults to "default".

    Returns:
        A fully-constructed ``RetrievalPipeline``.  Never raises.
    """
    root = Path(brain_root) if brain_root else Path(".")

    # ── Build HybridSearcher ──────────────────────────────────────────────────
    try:
        if HybridSearcher is None or make_embedder is None:
            raise ImportError("HybridSearcher or make_embedder not available")
        embedder = make_embedder()
        searcher = HybridSearcher(
            brain_root=root,
            workspace_slug=workspace_slug,
            embedder=embedder,
        )
        log.debug("[factory] HybridSearcher built",
                  brain_root=str(root), workspace_slug=workspace_slug)
    except Exception as exc:
        log.warning("[factory] HybridSearcher construction failed — using stub",
                    error=str(exc))
        searcher = _StubSearcher()  # type: ignore[assignment]

    # ── Build Reranker (optional) ─────────────────────────────────────────────
    reranker = None
    if getattr(settings, "retrieval_rerank_enabled", True):
        model_name = getattr(settings, "reranker_model", "BAAI/bge-reranker-v2-m3")
        try:
            if Reranker is None:
                raise ImportError("Reranker not available")
            reranker = Reranker(model_name=model_name)
            log.debug("[factory] Reranker built (lazy-load)", model=model_name)
        except Exception as exc:
            log.warning("[factory] Reranker construction failed — reranking disabled",
                        error=str(exc))

    top_candidates = getattr(settings, "retrieval_rerank_top_candidates", 50)
    rerank_enabled = bool(getattr(settings, "retrieval_rerank_enabled", True))

    return RetrievalPipeline(
        searcher=searcher,
        reranker=reranker,
        rerank_enabled=rerank_enabled,
        top_candidates=top_candidates,
    )


# ── Stub searcher for graceful degradation ────────────────────────────────────

class _StubSearcher:
    """Returns empty hits when HybridSearcher cannot be constructed.

    This allows the pipeline to be instantiated and used without crashing even
    when the brain root or Qdrant client is unavailable at construction time.
    """

    def search(self, query: str, **kwargs):  # noqa: ARG002
        log.debug("[factory._StubSearcher] search called — returning []")
        return []
