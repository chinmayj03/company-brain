"""
Reranker — cross-encoder reranking using bge-reranker-v2-m3.

Cross-encoders score (query, document) pairs jointly — much higher precision
than bi-encoder cosine similarity. Used as the final stage over the union of
BM25 + dense candidates.

Model: BAAI/bge-reranker-v2-m3 (multilingual, code-aware)
  - 0.6B params, runs on CPU in ~200ms for 50 candidates
  - Available on HuggingFace, no API key needed
  - Falls back to score passthrough if sentence-transformers not installed

Usage::
    reranker = Reranker()
    ranked = reranker.rerank(
        query="getPayerCompetitors competitiveness",
        candidates=[("path/to/file.java", "class CompetitivenessController..."), ...],
        top_k=10,
    )
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import structlog

log = structlog.get_logger(__name__)


@dataclass
class RankedResult:
    relative_path: str
    rerank_score: float
    original_score: float   # BM25 or cosine score before reranking


class Reranker:
    """
    Cross-encoder reranker. Lazy-loads the model on first use.
    Falls back to passthrough if sentence-transformers is unavailable.
    """

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3"):
        self._model_name = model_name
        self._model = None
        self._available: Optional[bool] = None

    def _load(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            from sentence_transformers import CrossEncoder  # type: ignore
            self._model = CrossEncoder(
                self._model_name,
                max_length=512,
                device="cpu",      # CPU is fine for 50 candidates
            )
            self._available = True
            log.info("Reranker loaded", model=self._model_name)
        except ImportError:
            log.warning("sentence-transformers not installed — reranking disabled (passthrough mode)")
            self._available = False
        except Exception as e:
            log.warning("Reranker load failed", error=str(e), model=self._model_name)
            self._available = False
        return self._available

    def rerank(
        self,
        query: str,
        candidates: list[tuple[str, str, float]],  # (relative_path, content_snippet, original_score)
        top_k: int = 10,
    ) -> list[RankedResult]:
        """
        Rerank candidates using the cross-encoder.

        Args:
            query:      The search query.
            candidates: List of (relative_path, content_snippet, original_score).
                        content_snippet is truncated to 500 chars for speed.
            top_k:      Number of results to return.

        Returns:
            Top-k results sorted by rerank_score descending.
        """
        if not candidates:
            return []

        if not self._load() or self._model is None:
            # Passthrough: sort by original score
            return [
                RankedResult(path, orig, orig)
                for path, _, orig in sorted(candidates, key=lambda x: x[2], reverse=True)[:top_k]
            ]

        pairs = [(query, snippet[:500]) for _, snippet, _ in candidates]
        try:
            scores = self._model.predict(pairs, show_progress_bar=False)
            results = [
                RankedResult(
                    relative_path=candidates[i][0],
                    rerank_score=float(scores[i]),
                    original_score=candidates[i][2],
                )
                for i in range(len(candidates))
            ]
            results.sort(key=lambda r: r.rerank_score, reverse=True)
            return results[:top_k]
        except Exception as e:
            log.warning("Reranking failed", error=str(e))
            return [
                RankedResult(path, orig, orig)
                for path, _, orig in sorted(candidates, key=lambda x: x[2], reverse=True)[:top_k]
            ]
