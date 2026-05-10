try:
    from companybrain.retrieval.hybrid_search import HybridSearcher, SearchHit
    from companybrain.retrieval.hybrid_search import FileHybridSearcher, SearchResult  # legacy
    from companybrain.retrieval.qdrant_store import QdrantBrainStore
except ImportError:
    # Optional heavy deps (bm25s, qdrant-client) may not be installed in all
    # environments (e.g. CI, test runners that only install core deps).
    # Callers that need these classes import them directly from their submodule.
    HybridSearcher = None  # type: ignore[assignment,misc]
    SearchHit = None  # type: ignore[assignment,misc]
    FileHybridSearcher = None  # type: ignore[assignment,misc]
    SearchResult = None  # type: ignore[assignment,misc]
    QdrantBrainStore = None  # type: ignore[assignment,misc]

__all__ = [
    "HybridSearcher", "SearchHit",         # ADR-0015
    "FileHybridSearcher", "SearchResult",   # legacy / backward compat
    "QdrantBrainStore",                     # ADR-0015
]
