from companybrain.retrieval.hybrid_search import HybridSearcher, SearchHit
from companybrain.retrieval.hybrid_search import FileHybridSearcher, SearchResult  # legacy
from companybrain.retrieval.qdrant_store import QdrantBrainStore
from companybrain.retrieval.pipeline import RetrievalPipeline, RetrievalResult  # ADR-0015 A1.2
from companybrain.retrieval.factory import make_retrieval_pipeline              # ADR-0015 A1.2

__all__ = [
    "HybridSearcher", "SearchHit",                  # ADR-0015
    "FileHybridSearcher", "SearchResult",            # legacy / backward compat
    "QdrantBrainStore",                              # ADR-0015
    "RetrievalPipeline", "RetrievalResult",          # ADR-0015 A1.2
    "make_retrieval_pipeline",                       # ADR-0015 A1.2
]
