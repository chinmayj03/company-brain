"""
Unit tests for ADR-0015 A1.2 — make_retrieval_pipeline factory.

Verifies that the factory builds the correct pipeline from Settings and
degrades gracefully when Qdrant or sentence-transformers are unavailable.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


from companybrain.retrieval.pipeline import RetrievalPipeline, RetrievalResult


# ── Minimal settings fixture ──────────────────────────────────────────────────

def _settings(
    rerank_enabled: bool = True,
    top_candidates: int = 50,
    reranker_model: str = "BAAI/bge-reranker-v2-m3",
):
    return SimpleNamespace(
        retrieval_rerank_enabled=rerank_enabled,
        retrieval_rerank_top_candidates=top_candidates,
        reranker_model=reranker_model,
        hybrid_search_enabled=True,
    )


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_factory_returns_retrieval_pipeline():
    from companybrain.retrieval.factory import make_retrieval_pipeline

    with (
        patch("companybrain.retrieval.factory.HybridSearcher") as MockSearcher,
        patch("companybrain.retrieval.factory.make_embedder") as mock_embedder,
        patch("companybrain.retrieval.factory.Reranker") as MockReranker,
    ):
        mock_embedder.return_value = MagicMock()
        MockSearcher.return_value = MagicMock()
        MockReranker.return_value = MagicMock()

        pipeline = make_retrieval_pipeline(_settings())

    assert isinstance(pipeline, RetrievalPipeline)


def test_factory_builds_reranker_when_enabled():
    from companybrain.retrieval.factory import make_retrieval_pipeline

    with (
        patch("companybrain.retrieval.factory.HybridSearcher"),
        patch("companybrain.retrieval.factory.make_embedder"),
        patch("companybrain.retrieval.factory.Reranker") as MockReranker,
    ):
        mock_reranker = MagicMock()
        MockReranker.return_value = mock_reranker

        pipeline = make_retrieval_pipeline(_settings(rerank_enabled=True))

    assert pipeline._reranker is mock_reranker
    assert pipeline._rerank_enabled is True


def test_factory_skips_reranker_when_disabled():
    from companybrain.retrieval.factory import make_retrieval_pipeline

    with (
        patch("companybrain.retrieval.factory.HybridSearcher"),
        patch("companybrain.retrieval.factory.make_embedder"),
        patch("companybrain.retrieval.factory.Reranker") as MockReranker,
    ):
        pipeline = make_retrieval_pipeline(_settings(rerank_enabled=False))

    MockReranker.assert_not_called()
    assert pipeline._reranker is None
    assert pipeline._rerank_enabled is False


def test_factory_propagates_top_candidates():
    from companybrain.retrieval.factory import make_retrieval_pipeline

    with (
        patch("companybrain.retrieval.factory.HybridSearcher"),
        patch("companybrain.retrieval.factory.make_embedder"),
        patch("companybrain.retrieval.factory.Reranker"),
    ):
        pipeline = make_retrieval_pipeline(_settings(top_candidates=35))

    assert pipeline._top_candidates == 35


def test_factory_uses_reranker_model_from_settings():
    from companybrain.retrieval.factory import make_retrieval_pipeline

    with (
        patch("companybrain.retrieval.factory.HybridSearcher"),
        patch("companybrain.retrieval.factory.make_embedder"),
        patch("companybrain.retrieval.factory.Reranker") as MockReranker,
    ):
        make_retrieval_pipeline(_settings(reranker_model="my-custom-model"))

    MockReranker.assert_called_once_with(model_name="my-custom-model")


def test_factory_graceful_when_qdrant_unavailable():
    """Factory must not raise even when HybridSearcher constructor throws."""
    from companybrain.retrieval.factory import make_retrieval_pipeline

    with (
        patch(
            "companybrain.retrieval.factory.HybridSearcher",
            side_effect=ConnectionRefusedError("qdrant is down"),
        ),
        patch("companybrain.retrieval.factory.make_embedder"),
        patch("companybrain.retrieval.factory.Reranker"),
    ):
        pipeline = make_retrieval_pipeline(_settings())

    # Should not raise; pipeline should be usable (stub searcher)
    assert isinstance(pipeline, RetrievalPipeline)


def test_factory_graceful_when_sentence_transformers_missing():
    """Factory must not raise even when sentence-transformers is not installed."""
    from companybrain.retrieval.factory import make_retrieval_pipeline

    with (
        patch("companybrain.retrieval.factory.HybridSearcher"),
        patch("companybrain.retrieval.factory.make_embedder"),
        patch(
            "companybrain.retrieval.factory.Reranker",
            side_effect=ImportError("sentence-transformers not installed"),
        ),
    ):
        pipeline = make_retrieval_pipeline(_settings(rerank_enabled=True))

    assert isinstance(pipeline, RetrievalPipeline)
    assert pipeline._reranker is None


def test_factory_stub_searcher_returns_empty_results():
    """When HybridSearcher fails, the stub searcher returns [] without crashing."""
    from companybrain.retrieval.factory import make_retrieval_pipeline

    with (
        patch(
            "companybrain.retrieval.factory.HybridSearcher",
            side_effect=RuntimeError("boom"),
        ),
        patch("companybrain.retrieval.factory.make_embedder"),
        patch("companybrain.retrieval.factory.Reranker"),
    ):
        pipeline = make_retrieval_pipeline(_settings())

    result = asyncio.get_event_loop().run_until_complete(
        pipeline.retrieve("test query", workspace_id="ws")
    )
    assert isinstance(result, RetrievalResult)
    assert result.hits == []
    assert result.top_score == 0.0


def test_factory_passes_brain_root_and_slug():
    from companybrain.retrieval.factory import make_retrieval_pipeline

    with (
        patch("companybrain.retrieval.factory.HybridSearcher") as MockSearcher,
        patch("companybrain.retrieval.factory.make_embedder") as mock_embedder,
        patch("companybrain.retrieval.factory.Reranker"),
    ):
        mock_embedder.return_value = MagicMock()
        make_retrieval_pipeline(
            _settings(),
            brain_root="/tmp/brain",
            workspace_slug="my-workspace",
        )

    call_kwargs = MockSearcher.call_args[1]
    assert call_kwargs["brain_root"] == Path("/tmp/brain")
    assert call_kwargs["workspace_slug"] == "my-workspace"


def test_factory_default_brain_root_is_cwd():
    from companybrain.retrieval.factory import make_retrieval_pipeline

    with (
        patch("companybrain.retrieval.factory.HybridSearcher") as MockSearcher,
        patch("companybrain.retrieval.factory.make_embedder"),
        patch("companybrain.retrieval.factory.Reranker"),
    ):
        make_retrieval_pipeline(_settings())

    call_kwargs = MockSearcher.call_args[1]
    assert call_kwargs["brain_root"] == Path(".")
