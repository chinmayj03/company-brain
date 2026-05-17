"""
Embedding-based semantic matcher for entity resolution (ADR-0093).

Uses ``sentence-transformers`` for local embedding computation.  The import
is **lazy** — if the package is not installed the module degrades gracefully:
``is_available()`` returns False and ``cosine_similarity()`` raises
``EmbedMatcherUnavailable``.

Model default: ``all-MiniLM-L6-v2`` (22M params, 384-dim, fast CPU inference).
Override via the ``RESOLUTION_EMBED_MODEL`` environment variable or the
``model_name`` constructor argument.

Thread safety: the SentenceTransformer model is loaded once and reused.
"""
from __future__ import annotations

import os
from typing import Optional

_SENTINEL = object()
_model_cache: dict[str, object] = {}


class EmbedMatcherUnavailable(RuntimeError):
    """Raised when sentence-transformers is not installed."""


def is_available() -> bool:
    """Return True when sentence-transformers can be imported."""
    try:
        import sentence_transformers  # noqa: F401
        return True
    except ImportError:
        return False


def _get_model(model_name: str):
    """Load (and cache) a SentenceTransformer model by name."""
    if model_name not in _model_cache:
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError as exc:
            raise EmbedMatcherUnavailable(
                "sentence-transformers is not installed. "
                "Run: pip install sentence-transformers"
            ) from exc
        _model_cache[model_name] = SentenceTransformer(model_name)
    return _model_cache[model_name]


class EmbedMatcher:
    """
    Computes cosine similarity between two text snippets using local embeddings.

    Parameters
    ----------
    model_name:
        HuggingFace model identifier.  Defaults to the ``RESOLUTION_EMBED_MODEL``
        env var, then ``all-MiniLM-L6-v2``.
    threshold:
        Minimum cosine similarity to consider two snippets a match.
        Defaults to the ``RESOLUTION_EMBED_THRESHOLD`` env var (0.80).
    """

    DEFAULT_MODEL = "all-MiniLM-L6-v2"
    DEFAULT_THRESHOLD = 0.80

    def __init__(
        self,
        model_name: Optional[str] = None,
        threshold: Optional[float] = None,
    ) -> None:
        self.model_name = (
            model_name
            or os.environ.get("RESOLUTION_EMBED_MODEL", self.DEFAULT_MODEL)
        )
        self.threshold = (
            threshold
            if threshold is not None
            else float(os.environ.get("RESOLUTION_EMBED_THRESHOLD", self.DEFAULT_THRESHOLD))
        )

    def similarity(self, text_a: str, text_b: str) -> float:
        """
        Return cosine similarity in [0, 1].

        Raises
        ------
        EmbedMatcherUnavailable
            When sentence-transformers is not installed.
        """
        import numpy as np  # numpy is a common dep; if absent, fail loudly

        model = _get_model(self.model_name)
        vecs = model.encode([text_a, text_b], normalize_embeddings=True)
        return float(np.dot(vecs[0], vecs[1]))

    def matches(self, text_a: str, text_b: str) -> tuple[bool, float]:
        """
        Return ``(is_match, similarity_score)``.

        Returns ``(False, 0.0)`` when sentence-transformers is unavailable
        (graceful degradation — the caller should fall through to a lower tier).
        """
        try:
            score = self.similarity(text_a, text_b)
            return score >= self.threshold, score
        except EmbedMatcherUnavailable:
            return False, 0.0
        except Exception:  # pragma: no cover
            return False, 0.0
