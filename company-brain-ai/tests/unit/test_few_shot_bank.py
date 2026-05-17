"""
Unit tests for FewShotBank (A1.7).

Tests:
  - Add example, verify persisted to JSON file
  - Eviction at max_per_bucket: lowest quality evicted first
  - Multi-workspace isolation: different workspace IDs don't share examples
  - Multi-persona isolation
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from companybrain.workspace.few_shot.bank import FewShotBank, FewShotExample


# ── Fixture helpers ────────────────────────────────────────────────────────────

def _make_example(
    workspace_id: str = "ws-1",
    persona: str = "developer",
    question: str = "What does PaymentService do?",
    answer: str = "It processes payments.",
    quality_score: float = 0.8,
    example_id: str | None = None,
) -> FewShotExample:
    now = datetime.now(tz=timezone.utc)
    return FewShotExample(
        id=example_id or "test-id",
        workspace_id=workspace_id,
        persona=persona,
        question=question,
        answer=answer,
        citations=["urn:cb:function:PaymentService"],
        quality_score=quality_score,
        embedding=[0.1, 0.2, 0.3],
        created_at=now,
        last_used_at=now,
        use_count=0,
    )


@pytest.fixture()
def bank(tmp_path: Path) -> FewShotBank:
    return FewShotBank(storage_path=tmp_path / "few_shot", max_per_bucket=5)


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestAddAndPersist:
    def test_add_example_persists_to_json(self, bank: FewShotBank, tmp_path: Path):
        ex = _make_example(example_id="abc-123")
        bank.add(ex)

        bucket_file = tmp_path / "few_shot" / "ws-1" / "developer.json"
        assert bucket_file.exists(), "JSON file should be created on add"

        rows = json.loads(bucket_file.read_text())
        assert len(rows) == 1
        assert rows[0]["id"] == "abc-123"
        assert rows[0]["question"] == "What does PaymentService do?"
        assert rows[0]["quality_score"] == pytest.approx(0.8)

    def test_add_multiple_examples_accumulates(self, bank: FewShotBank):
        for i in range(3):
            bank.add(_make_example(example_id=f"id-{i}", question=f"Q {i}"))

        examples = bank.get_all("ws-1", "developer")
        assert len(examples) == 3

    def test_get_all_returns_empty_for_missing_bucket(self, bank: FewShotBank):
        result = bank.get_all("nonexistent-ws", "pm")
        assert result == []

    def test_roundtrip_preserves_fields(self, bank: FewShotBank, tmp_path: Path):
        ex = _make_example(example_id="round-1")
        bank.add(ex)

        # Force reload from disk by clearing the cache
        bank._cache.clear()
        loaded = bank.get_all("ws-1", "developer")
        assert len(loaded) == 1
        assert loaded[0].id == "round-1"
        assert loaded[0].embedding == [0.1, 0.2, 0.3]
        assert loaded[0].citations == ["urn:cb:function:PaymentService"]


class TestEviction:
    def test_evicts_lowest_quality_first(self, bank: FewShotBank):
        """When bucket exceeds max_per_bucket, lowest quality_score evicted first."""
        # Fill to max (5) + 1 extra
        for i in range(5):
            bank.add(_make_example(
                example_id=f"high-{i}",
                question=f"High quality Q {i}",
                quality_score=0.9,
            ))
        # Add one low-quality example — this should trigger eviction
        bank.add(_make_example(
            example_id="low-quality",
            question="Low quality Q",
            quality_score=0.1,
        ))

        examples = bank.get_all("ws-1", "developer")
        assert len(examples) <= 5
        ids = {ex.id for ex in examples}
        assert "low-quality" not in ids, "Low quality example should be evicted first"

    def test_evict_if_needed_returns_count(self, bank: FewShotBank):
        # Add max+2 examples manually (bypass eviction-on-add by manipulating cache)
        bucket = []
        for i in range(7):
            now = datetime.now(tz=timezone.utc)
            bucket.append(FewShotExample(
                id=f"eid-{i}",
                workspace_id="ws-1",
                persona="developer",
                question=f"Q {i}",
                answer="A",
                citations=["urn:cb:fn:x"],
                quality_score=0.5 + i * 0.05,  # increasing quality
                embedding=[],
                created_at=now,
                last_used_at=now,
                use_count=0,
            ))
        bank._cache[("ws-1", "developer")] = bucket
        bank._flush("ws-1", "developer", bucket)

        evicted = bank.evict_if_needed("ws-1", "developer")
        assert evicted == 2
        remaining = bank.get_all("ws-1", "developer")
        assert len(remaining) == 5

    def test_evict_keeps_highest_quality(self, bank: FewShotBank):
        """After eviction, the retained examples should have the highest scores."""
        now = datetime.now(tz=timezone.utc)
        bucket = []
        for i in range(7):
            bucket.append(FewShotExample(
                id=f"q-{i}",
                workspace_id="ws-1",
                persona="developer",
                question=f"Q {i}",
                answer="A",
                citations=["urn:cb:fn:x"],
                quality_score=round(0.1 * (i + 1), 1),  # 0.1, 0.2, ..., 0.7
                embedding=[],
                created_at=now,
                last_used_at=now,
                use_count=0,
            ))
        bank._cache[("ws-1", "developer")] = bucket
        bank._flush("ws-1", "developer", bucket)

        bank.evict_if_needed("ws-1", "developer")
        remaining = bank.get_all("ws-1", "developer")
        scores = sorted([ex.quality_score for ex in remaining])
        # Lowest two (0.1, 0.2) should be gone; top 5 (0.3..0.7) retained
        assert min(scores) == pytest.approx(0.3), f"Expected min 0.3, got {scores}"


class TestWorkspaceIsolation:
    def test_different_workspaces_do_not_share_examples(self, bank: FewShotBank):
        bank.add(_make_example(workspace_id="ws-alpha", example_id="a1"))
        bank.add(_make_example(workspace_id="ws-beta",  example_id="b1"))

        alpha = bank.get_all("ws-alpha", "developer")
        beta  = bank.get_all("ws-beta",  "developer")

        assert [ex.id for ex in alpha] == ["a1"]
        assert [ex.id for ex in beta]  == ["b1"]

    def test_workspace_files_are_separate_directories(self, bank: FewShotBank, tmp_path: Path):
        bank.add(_make_example(workspace_id="ws-a", example_id="x1"))
        bank.add(_make_example(workspace_id="ws-b", example_id="x2"))

        assert (tmp_path / "few_shot" / "ws-a" / "developer.json").exists()
        assert (tmp_path / "few_shot" / "ws-b" / "developer.json").exists()


class TestPersonaIsolation:
    def test_different_personas_do_not_share_examples(self, bank: FewShotBank):
        bank.add(_make_example(persona="developer", example_id="dev-1"))
        bank.add(_make_example(persona="pm",         example_id="pm-1"))

        devs = bank.get_all("ws-1", "developer")
        pms  = bank.get_all("ws-1", "pm")

        assert [ex.id for ex in devs] == ["dev-1"]
        assert [ex.id for ex in pms]  == ["pm-1"]

    def test_persona_files_are_separate_json_files(self, bank: FewShotBank, tmp_path: Path):
        bank.add(_make_example(persona="developer", example_id="d1"))
        bank.add(_make_example(persona="vp_eng",    example_id="v1"))

        assert (tmp_path / "few_shot" / "ws-1" / "developer.json").exists()
        assert (tmp_path / "few_shot" / "ws-1" / "vp_eng.json").exists()


class TestDelete:
    def test_delete_removes_example(self, bank: FewShotBank):
        bank.add(_make_example(example_id="del-me"))
        bank.add(_make_example(example_id="keep-me", question="Other Q"))

        bank.delete("del-me")

        remaining = bank.get_all("ws-1", "developer")
        ids = {ex.id for ex in remaining}
        assert "del-me" not in ids
        assert "keep-me" in ids

    def test_delete_nonexistent_is_noop(self, bank: FewShotBank):
        bank.add(_make_example(example_id="exists"))
        bank.delete("does-not-exist")  # must not raise
        assert len(bank.get_all("ws-1", "developer")) == 1
