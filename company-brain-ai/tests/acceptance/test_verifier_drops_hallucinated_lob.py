"""
ADR-0056 acceptance — the lob-query failure two days ago must no longer slip
through to /query responses once the verifier is on.

This test reconstructs that exact failure shape:
  - A real ``CompetitivenessPlanRepository.java`` repo file contains the real
    query (a JPQL ``SELECT ... WHERE planId = :planId`` style).
  - The extractor (mocked) emits a ``DatabaseQuery`` entity whose
    ``query_text`` references a table that does not exist in source.
  - VerifierLoop is run against the entity + the real source root.
  - Mode A alone (sub-agent disabled, no network) is enough to mark it
    hallucinated, and the /query verified-filter drops it.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from companybrain.api.routes.query import _filter_verified
from companybrain.models.entities import ExtractedEntity
from companybrain.pipeline.verifier_loop import VerifierLoop


pytestmark = pytest.mark.acceptance


class _FakeHit:
    """Mirror of HybridSearcher's `.urn` + `.payload` surface."""

    def __init__(self, urn: str, verified: str):
        self.urn = urn
        self.payload = {
            "qualified_name": urn.split(":")[-1],
            "entity_type": "DatabaseQuery",
            "verified": verified,
        }


_REAL_REPOSITORY = """\
package com.example.competitiveness.repo;

import org.springframework.data.jpa.repository.Query;

public interface CompetitivenessPlanRepository {
    @Query("SELECT cp FROM CompetitivenessPlan cp WHERE cp.planId = :planId")
    CompetitivenessPlan findByPlanId(String planId);

    @Query("SELECT pc FROM PayerCompetitor pc WHERE pc.payerId = :payerId AND pc.tier = :tier")
    java.util.List<PayerCompetitor> getPayerCompetitors(String payerId, String tier);
}
"""


async def test_hallucinated_query_text_is_dropped_from_query_response(tmp_path):
    repo_root = tmp_path / "apps" / "competitiveness"
    repo_file = (
        repo_root
        / "src" / "main" / "java" / "com" / "example" / "competitiveness" / "repo"
        / "CompetitivenessPlanRepository.java"
    )
    repo_file.parent.mkdir(parents=True, exist_ok=True)
    repo_file.write_text(_REAL_REPOSITORY, encoding="utf-8")

    rel_path = str(repo_file.relative_to(repo_root))

    # The hallucinated entity the LLM emitted on the failing run — a SELECT
    # over a table that does not exist anywhere in source.
    fake = ExtractedEntity(
        entity_type="DatabaseQuery",
        name="getPayerCompetitors.query",
        file=rel_path,
        repo="competitiveness",
        signature="JPQL query",
        last_modified_commit="deadbeef",
        confidence=0.9,
        query_text="SELECT * FROM nonexistent_table WHERE never_in_source = 1",
    )
    # And a legitimate entity that DOES match source — must survive.
    real = ExtractedEntity(
        entity_type="DatabaseQuery",
        name="findByPlanId.query",
        file=rel_path,
        repo="competitiveness",
        signature="JPQL query",
        last_modified_commit="deadbeef",
        confidence=0.9,
        query_text="SELECT cp FROM CompetitivenessPlan cp WHERE cp.planId = :planId",
    )

    loop = VerifierLoop(enable_subagent=False, enable_self_correction=False)
    out, stats = await loop.run([fake, real], source_roots=[repo_root])

    by_name = {e.name: e for e in out}
    assert by_name["getPayerCompetitors.query"].verified == "hallucinated", (
        "lob-query-shaped hallucination must be flagged by Mode A"
    )
    assert by_name["findByPlanId.query"].verified == "confirmed", (
        "a real JPQL query verbatim in source must be confirmed"
    )
    assert stats.hallucinated == 1
    assert stats.confirmed == 1

    # ── Simulate /query retrieval and apply the verified filter ──────────
    hits = [
        _FakeHit("urn:fake", by_name["getPayerCompetitors.query"].verified),
        _FakeHit("urn:real", by_name["findByPlanId.query"].verified),
    ]
    surfaced = _filter_verified(hits, include_unverified=False)
    surfaced_urns = {h.urn for h in surfaced}
    assert "urn:fake" not in surfaced_urns, (
        "hallucinated entity must be excluded from /query by default"
    )
    assert "urn:real" in surfaced_urns

    # Opting in must surface both, so callers debugging extraction quality
    # can still see what was dropped.
    surfaced_with_flag = _filter_verified(hits, include_unverified=True)
    assert {h.urn for h in surfaced_with_flag} == {"urn:fake", "urn:real"}
