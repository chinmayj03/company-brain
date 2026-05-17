"""
Few-shot retriever: return the top-k most similar exemplars for a query.

Primary:  sentence-transformers ``all-MiniLM-L6-v2`` → cosine similarity
Fallback: BM25-style token overlap (no extra deps required)

Never raises — returns [] on any error so /query is never blocked by the bank.
"""
from __future__ import annotations

import math
import re
from typing import List

import structlog

from companybrain.workspace.few_shot.bank import FewShotBank, FewShotExample

log = structlog.get_logger(__name__)

# ── Embedding layer (optional) ────────────────────────────────────────────────

_MODEL = None          # lazy-loaded SentenceTransformer
_ST_AVAILABLE = None   # None = not yet checked


def _get_model():
    global _MODEL, _ST_AVAILABLE
    if _ST_AVAILABLE is False:
        return None
    if _MODEL is not None:
        return _MODEL
    try:
        from sentence_transformers import SentenceTransformer
        _MODEL = SentenceTransformer("all-MiniLM-L6-v2")
        _ST_AVAILABLE = True
        log.info("few_shot.retriever.model_loaded", model="all-MiniLM-L6-v2")
    except Exception as exc:
        _ST_AVAILABLE = False
        log.info("few_shot.retriever.no_sentence_transformers", error=str(exc))
        _MODEL = None
    return _MODEL


def _embed(text: str) -> List[float]:
    model = _get_model()
    if model is None:
        return []
    try:
        vec = model.encode(text, normalize_embeddings=True)
        return vec.tolist()
    except Exception as exc:
        log.warning("few_shot.retriever.embed_failed", error=str(exc))
        return []


# ── Cosine similarity (numpy) ─────────────────────────────────────────────────

def _cosine(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two float lists. Returns 0.0 on error."""
    if not a or not b or len(a) != len(b):
        return 0.0
    try:
        import numpy as np
        va = np.asarray(a, dtype=float)
        vb = np.asarray(b, dtype=float)
        norm_a = np.linalg.norm(va)
        norm_b = np.linalg.norm(vb)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(np.dot(va, vb) / (norm_a * norm_b))
    except Exception:
        # Manual dot product fallback
        dot   = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a))
        mag_b = math.sqrt(sum(x * x for x in b))
        if mag_a == 0 or mag_b == 0:
            return 0.0
        return dot / (mag_a * mag_b)


# ── BM25-style keyword overlap ────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")


def _tokenize(text: str) -> List[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _bm25_score(query_tokens: List[str], doc_tokens: List[str]) -> float:
    """
    Simple BM25-inspired overlap score (no IDF, no BM25 tuning params).
    Returns the Jaccard-weighted overlap fraction as a proxy for similarity.
    """
    if not query_tokens or not doc_tokens:
        return 0.0
    q_set  = set(query_tokens)
    d_set  = set(doc_tokens)
    inter  = q_set & d_set
    union  = q_set | d_set
    return len(inter) / len(union) if union else 0.0


# ── Retriever ─────────────────────────────────────────────────────────────────

class FewShotRetriever:
    """
    Return top-k FewShotExamples most similar to ``question``.

    Uses sentence-transformers when available; falls back to BM25 token overlap.
    Updates ``last_used_at`` and ``use_count`` on every retrieved example so the
    eviction policy sees accurate recency data.
    """

    def __init__(self, bank: FewShotBank) -> None:
        self._bank = bank

    async def retrieve(
        self,
        question: str,
        workspace_id: str,
        persona: str,
        top_k: int = 3,
    ) -> List[FewShotExample]:
        """
        Return top-k examples by similarity.

        Tries cosine similarity first; falls back to BM25 if embeddings are
        absent or the sentence-transformers library is not installed.
        Never raises — returns [] on any error.
        """
        try:
            examples = self._bank.get_all(workspace_id, persona)
            if not examples:
                return []

            query_embedding = _embed(question)
            use_embeddings  = bool(query_embedding)

            if use_embeddings:
                scored = self._score_cosine(question, query_embedding, examples)
            else:
                scored = self._score_bm25(question, examples)

            top = sorted(scored, key=lambda x: x[1], reverse=True)[:top_k]

            # Update usage metadata
            from datetime import datetime, timezone
            import time
            now = datetime.now(tz=timezone.utc)
            result: List[FewShotExample] = []
            for ex, _score in top:
                ex.last_used_at = now
                ex.use_count   += 1
                result.append(ex)

            # Persist usage updates
            if result:
                all_examples = self._bank.get_all(workspace_id, persona)
                # Flush is triggered implicitly — the bank's in-memory list was
                # mutated in place (same objects), so we call _flush directly.
                self._bank._flush(workspace_id, persona, all_examples)

            return result

        except Exception as exc:
            log.warning("few_shot.retriever.retrieve_failed", error=str(exc))
            return []

    # ── Scoring helpers ───────────────────────────────────────────────────────

    def _score_cosine(
        self,
        question: str,
        query_embedding: List[float],
        examples: List[FewShotExample],
    ) -> List[tuple]:
        scored = []
        for ex in examples:
            if ex.embedding:
                sim = _cosine(query_embedding, ex.embedding)
            else:
                # No stored embedding → fall back to BM25 for this example
                q_toks = _tokenize(question)
                d_toks = _tokenize(ex.question)
                sim    = _bm25_score(q_toks, d_toks)
            scored.append((ex, sim))
        return scored

    def _score_bm25(
        self,
        question: str,
        examples: List[FewShotExample],
    ) -> List[tuple]:
        q_toks = _tokenize(question)
        return [
            (ex, _bm25_score(q_toks, _tokenize(ex.question)))
            for ex in examples
        ]
