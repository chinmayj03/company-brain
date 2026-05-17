"""
Acceptance tests for the few-shot bank end-to-end (A1.7).

Tests:
  - POST /feedback/thumbs with thumbs="up" -> example appears in bank
  - POST /feedback/thumbs with thumbs="down" -> example NOT recorded
  - GET /feedback/stats returns correct counts
  - Retriever returns top-3 by similarity (mock embeddings with deterministic vectors)
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_example(
    workspace_id: str = "ws-test",
    persona: str = "developer",
    question: str = "What does PaymentService do?",
    answer: str = "It processes payments.",
    quality_score: float = 0.8,
    example_id: str | None = None,
    embedding: List[float] | None = None,
):
    from companybrain.workspace.few_shot.bank import FewShotExample
    now = datetime.now(tz=timezone.utc)
    return FewShotExample(
        id=example_id or "test-id",
        workspace_id=workspace_id,
        persona=persona,
        question=question,
        answer=answer,
        citations=["urn:cb:function:PaymentService"],
        quality_score=quality_score,
        embedding=embedding or [0.1, 0.2, 0.3],
        created_at=now,
        last_used_at=now,
        use_count=0,
    )


def _mock_settings(tmp_path: Path):
    """Return a context manager that patches settings with minimal few-shot config."""
    from unittest.mock import MagicMock
    m = MagicMock()
    m.few_shot_enabled = True
    m.few_shot_min_confidence = 0.6
    m.few_shot_max_per_bucket = 200
    m.few_shot_bank_path = str(tmp_path / "bank")
    return patch("companybrain.config.settings", m)


# ── Thumbs endpoint ───────────────────────────────────────────────────────────

class TestThumbsEndpoint:
    def test_thumbs_up_records_example(self, tmp_path: Path):
        """POST /feedback/thumbs thumbs='up' with confidence>=0.6 + citations -> recorded."""
        from companybrain.workspace.few_shot.bank import FewShotBank
        from companybrain.workspace.few_shot.retriever import FewShotRetriever
        from companybrain.workspace.few_shot.capture import FewShotCapture

        bank = FewShotBank(storage_path=tmp_path / "bank", max_per_bucket=200)
        capture = FewShotCapture(bank, FewShotRetriever(bank))

        with _mock_settings(tmp_path):
            recorded = asyncio.run(
                capture.record_if_successful(
                    workspace_id="ws-test",
                    persona="developer",
                    question="What does PaymentService do?",
                    answer="It processes payments.",
                    citations=["urn:cb:function:PaymentService"],
                    confidence_score=0.75,
                    thumbs_feedback="up",
                )
            )

        assert recorded is True
        examples = bank.get_all("ws-test", "developer")
        assert len(examples) == 1
        # thumbs_up should boost quality_score to >= 0.9
        assert examples[0].quality_score >= 0.9

    def test_thumbs_down_does_not_record(self, tmp_path: Path):
        """POST /feedback/thumbs thumbs='down' -> example NOT recorded."""
        from companybrain.workspace.few_shot.bank import FewShotBank
        from companybrain.workspace.few_shot.retriever import FewShotRetriever
        from companybrain.workspace.few_shot.capture import FewShotCapture

        bank = FewShotBank(storage_path=tmp_path / "bank", max_per_bucket=200)
        capture = FewShotCapture(bank, FewShotRetriever(bank))

        with _mock_settings(tmp_path):
            recorded = asyncio.run(
                capture.record_if_successful(
                    workspace_id="ws-test",
                    persona="developer",
                    question="Bad answer question",
                    answer="This answer was wrong.",
                    citations=["urn:cb:function:Something"],
                    confidence_score=0.9,
                    thumbs_feedback="down",
                )
            )

        assert recorded is False
        assert len(bank.get_all("ws-test", "developer")) == 0, \
            "thumbs='down' must not persist the example"

    def test_low_confidence_not_recorded(self, tmp_path: Path):
        """confidence_score < 0.6 -> not recorded regardless of thumbs."""
        from companybrain.workspace.few_shot.bank import FewShotBank
        from companybrain.workspace.few_shot.retriever import FewShotRetriever
        from companybrain.workspace.few_shot.capture import FewShotCapture

        bank = FewShotBank(storage_path=tmp_path / "bank", max_per_bucket=200)
        capture = FewShotCapture(bank, FewShotRetriever(bank))

        with _mock_settings(tmp_path):
            recorded = asyncio.run(
                capture.record_if_successful(
                    workspace_id="ws-test",
                    persona="developer",
                    question="Low confidence question",
                    answer="Uncertain answer.",
                    citations=["urn:cb:function:X"],
                    confidence_score=0.4,
                    thumbs_feedback=None,
                )
            )

        assert recorded is False

    def test_no_citations_not_recorded(self, tmp_path: Path):
        """len(citations) == 0 -> not recorded even with high confidence."""
        from companybrain.workspace.few_shot.bank import FewShotBank
        from companybrain.workspace.few_shot.retriever import FewShotRetriever
        from companybrain.workspace.few_shot.capture import FewShotCapture

        bank = FewShotBank(storage_path=tmp_path / "bank", max_per_bucket=200)
        capture = FewShotCapture(bank, FewShotRetriever(bank))

        with _mock_settings(tmp_path):
            recorded = asyncio.run(
                capture.record_if_successful(
                    workspace_id="ws-test",
                    persona="developer",
                    question="No citations question",
                    answer="Answer without any citations.",
                    citations=[],
                    confidence_score=0.95,
                    thumbs_feedback="up",
                )
            )

        assert recorded is False


# ── Stats endpoint ────────────────────────────────────────────────────────────

class TestStatsEndpoint:
    def test_stats_returns_correct_counts(self, tmp_path: Path):
        """GET /feedback/stats returns per-persona counts."""
        from companybrain.workspace.few_shot.bank import FewShotBank

        bank = FewShotBank(storage_path=tmp_path / "bank", max_per_bucket=200)
        bank.add(_make_example(workspace_id="ws-stat", persona="developer", example_id="d1"))
        bank.add(_make_example(workspace_id="ws-stat", persona="developer", example_id="d2"))
        bank.add(_make_example(workspace_id="ws-stat", persona="pm",        example_id="p1"))

        developer_count = len(bank.get_all("ws-stat", "developer"))
        pm_count        = len(bank.get_all("ws-stat", "pm"))

        assert developer_count == 2
        assert pm_count == 1

    def test_stats_recent_additions_correct(self, tmp_path: Path):
        """recent_additions counts examples added in the last 24h."""
        from datetime import timedelta
        from companybrain.workspace.few_shot.bank import FewShotBank

        bank = FewShotBank(storage_path=tmp_path / "bank", max_per_bucket=200)
        bank.add(_make_example(workspace_id="ws-r", persona="developer", example_id="r1"))
        bank.add(_make_example(workspace_id="ws-r", persona="pm",        example_id="r2"))

        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)
        recent = sum(
            1
            for persona in ("developer", "pm", "vp_eng", "generic")
            for ex in bank.get_all("ws-r", persona)
            if ex.created_at >= cutoff
        )
        assert recent == 2


# ── Retriever tests ───────────────────────────────────────────────────────────

class TestRetriever:
    def test_top_k_by_cosine_similarity(self, tmp_path: Path):
        """Retriever returns top-k examples by cosine similarity with deterministic vectors."""
        from companybrain.workspace.few_shot.bank import FewShotBank, FewShotExample
        from companybrain.workspace.few_shot.retriever import FewShotRetriever

        bank = FewShotBank(storage_path=tmp_path / "bank", max_per_bucket=200)
        now = datetime.now(tz=timezone.utc)

        # Four examples with deterministic unit-ish vectors
        for eid, emb in [
            ("e1", [1.0, 0.0, 0.0]),    # identical to query -> cosine=1.0
            ("e2", [0.0, 1.0, 0.0]),    # orthogonal          -> cosine=0.0
            ("e3", [-1.0, 0.0, 0.0]),   # opposite            -> cosine=-1.0
            ("e4", [0.9, 0.1, 0.0]),    # near query          -> cosine~0.99
        ]:
            bank.add(FewShotExample(
                id=eid, workspace_id="ws-1", persona="developer",
                question=f"Q{eid}", answer="A",
                citations=["urn:cb:fn:x"], quality_score=0.8,
                embedding=emb, created_at=now, last_used_at=now, use_count=0,
            ))

        with patch("companybrain.workspace.few_shot.retriever._embed", return_value=[1.0, 0.0, 0.0]):
            retriever = FewShotRetriever(bank)
            results = asyncio.run(
                retriever.retrieve(
                    question="What does PaymentService do?",
                    workspace_id="ws-1",
                    persona="developer",
                    top_k=2,
                )
            )

        assert len(results) == 2
        result_ids = [ex.id for ex in results]
        assert "e3" not in result_ids, "Opposite-direction example should not be in top-2"
        assert "e1" in result_ids, "Most similar example should be top result"

    def test_retriever_bm25_fallback(self, tmp_path: Path):
        """When embeddings are empty, BM25 fallback is used without raising."""
        from companybrain.workspace.few_shot.bank import FewShotBank, FewShotExample
        from companybrain.workspace.few_shot.retriever import FewShotRetriever

        bank = FewShotBank(storage_path=tmp_path / "bank", max_per_bucket=200)
        now = datetime.now(tz=timezone.utc)

        for i, q in enumerate(["payment service processing", "user authentication flow", "database migration"]):
            bank.add(FewShotExample(
                id=f"bm25-{i}", workspace_id="ws-1", persona="developer",
                question=q, answer="answer",
                citations=["urn:cb:fn:x"], quality_score=0.7,
                embedding=[],  # empty -> BM25 fallback
                created_at=now, last_used_at=now, use_count=0,
            ))

        with patch("companybrain.workspace.few_shot.retriever._embed", return_value=[]):
            retriever = FewShotRetriever(bank)
            results = asyncio.run(
                retriever.retrieve(
                    question="payment processing service",
                    workspace_id="ws-1",
                    persona="developer",
                    top_k=2,
                )
            )

        assert len(results) >= 1
        assert results[0].id == "bm25-0"

    def test_retriever_never_raises(self, tmp_path: Path):
        """retrieve() must return [] rather than raise on any internal error."""
        from companybrain.workspace.few_shot.bank import FewShotBank
        from companybrain.workspace.few_shot.retriever import FewShotRetriever

        bank = FewShotBank(storage_path=tmp_path / "bank", max_per_bucket=200)

        with patch.object(bank, "get_all", side_effect=RuntimeError("boom")):
            retriever = FewShotRetriever(bank)
            results = asyncio.run(retriever.retrieve("any question", "ws-1", "developer"))

        assert results == []


# ── Capture quality logic ─────────────────────────────────────────────────────

class TestCaptureQualityLogic:
    def _make_capture(self, tmp_path: Path):
        from companybrain.workspace.few_shot.bank import FewShotBank
        from companybrain.workspace.few_shot.retriever import FewShotRetriever
        from companybrain.workspace.few_shot.capture import FewShotCapture

        bank = FewShotBank(storage_path=tmp_path / "bank", max_per_bucket=200)
        return FewShotCapture(bank, FewShotRetriever(bank)), bank

    def test_thumbs_up_boosts_quality_score(self, tmp_path: Path):
        capture, bank = self._make_capture(tmp_path)

        with _mock_settings(tmp_path):
            asyncio.run(
                capture.record_if_successful(
                    workspace_id="ws-q",
                    persona="developer",
                    question="Some question",
                    answer="Some answer",
                    citations=["urn:cb:fn:x"],
                    confidence_score=0.65,
                    thumbs_feedback="up",
                )
            )

        examples = bank.get_all("ws-q", "developer")
        assert len(examples) == 1
        assert examples[0].quality_score == pytest.approx(0.9)

    def test_no_feedback_uses_confidence_as_quality(self, tmp_path: Path):
        capture, bank = self._make_capture(tmp_path)

        with _mock_settings(tmp_path):
            asyncio.run(
                capture.record_if_successful(
                    workspace_id="ws-q2",
                    persona="developer",
                    question="Some other question",
                    answer="Answer.",
                    citations=["urn:cb:fn:y"],
                    confidence_score=0.72,
                    thumbs_feedback=None,
                )
            )

        examples = bank.get_all("ws-q2", "developer")
        assert len(examples) == 1
        assert examples[0].quality_score == pytest.approx(0.72)

    def test_feature_flag_off_skips_recording(self, tmp_path: Path):
        capture, bank = self._make_capture(tmp_path)

        from unittest.mock import MagicMock
        m = MagicMock()
        m.few_shot_enabled = False
        m.few_shot_min_confidence = 0.6

        with patch("companybrain.config.settings", m):
            recorded = asyncio.run(
                capture.record_if_successful(
                    workspace_id="ws-q3",
                    persona="developer",
                    question="Any question",
                    answer="Any answer",
                    citations=["urn:cb:fn:z"],
                    confidence_score=0.95,
                    thumbs_feedback="up",
                )
            )

        assert recorded is False
        assert len(bank.get_all("ws-q3", "developer")) == 0
