"""Unit tests for the ADR-0056 VerifierLoop (modes A / B / C)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from companybrain.agents.verifier_agent import SubagentVerdict, parse_verdict
from companybrain.models.entities import ExtractedEntity
from companybrain.pipeline.self_correction import (
    SelfCorrectionResult,
    is_high_stakes,
    should_self_correct,
)
from companybrain.pipeline.verifier_deterministic import (
    FUZZY_THRESHOLD,
    DeterministicResult,
    verify_entity,
)
from companybrain.pipeline.verifier_loop import VerifierLoop


# ── Test helpers ──────────────────────────────────────────────────────────────

def _make_entity(
    *,
    name: str = "FooService.bar",
    file: str = "Foo.java",
    query_text: str = "",
    code_snippet: str = "",
    confidence: float = 0.9,
    entity_type: str = "Function",
) -> ExtractedEntity:
    return ExtractedEntity(
        entity_type=entity_type,
        name=name,
        file=file,
        repo="repo-a",
        signature=f"public void {name}()",
        last_modified_commit="abc123",
        confidence=confidence,
        query_text=query_text,
        code_snippet=code_snippet,
    )


def _write(tmp: Path, rel: str, body: str) -> Path:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# ── Mode A: deterministic verifier ────────────────────────────────────────────

class TestDeterministicMode:
    def test_exact_substring_match_confirms(self, tmp_path):
        _write(tmp_path, "Foo.java",
               "class Foo { String q = \"SELECT * FROM users WHERE id = ?\"; }")
        entity = _make_entity(query_text="SELECT * FROM users WHERE id = ?")
        result = verify_entity(entity, [tmp_path])
        assert result.status == "confirmed"
        assert result.matched_field == "query_text"
        assert result.ratio == 1.0

    def test_whitespace_normalisation_still_confirms(self, tmp_path):
        # Source has extra spaces and a newline mid-claim — should still match.
        _write(tmp_path, "Foo.java",
               "class Foo {\n  String q =\n   \"SELECT    *  FROM   users\";\n}")
        entity = _make_entity(query_text="SELECT * FROM users")
        result = verify_entity(entity, [tmp_path])
        assert result.status == "confirmed"

    def test_case_insensitive_match(self, tmp_path):
        _write(tmp_path, "Foo.java", "SELECT * FROM users WHERE id = ?")
        entity = _make_entity(query_text="select * from users where id = ?")
        result = verify_entity(entity, [tmp_path])
        assert result.status == "confirmed"

    def test_hallucinated_query_marked(self, tmp_path):
        _write(tmp_path, "Foo.java",
               "class Foo { void getThings() { repo.findAll(); } }")
        entity = _make_entity(
            entity_type="DatabaseQuery",
            query_text="SELECT * FROM nonexistent_table WHERE never_in_source = 1",
        )
        result = verify_entity(entity, [tmp_path])
        assert result.status == "hallucinated"
        assert "no source match" in result.notes

    def test_empty_claim_skipped(self, tmp_path):
        entity = _make_entity()
        result = verify_entity(entity, [tmp_path])
        assert result.status == "skipped"
        assert "no claim text" in result.notes

    def test_missing_file_skipped(self, tmp_path):
        entity = _make_entity(query_text="SELECT 1", file="not-here.java")
        result = verify_entity(entity, [tmp_path])
        assert result.status == "skipped"
        assert "source file not found" in result.notes

    def test_fuzzy_match_above_threshold(self, tmp_path):
        # Claim differs from source by one identifier character — well under
        # the 5% Levenshtein budget the ADR allows.
        source_line = "competitorsService.getPayerCompetitors(payerId, tier)"
        claim_line  = "competitorsService.getPayerCompetitors(payerID, tier)"
        _write(tmp_path, "Foo.java",
               f"public void run() {{ {source_line}; }}")
        entity = _make_entity(code_snippet=claim_line)
        result = verify_entity(entity, [tmp_path])
        # Either confirmed (after normalisation) or fuzzy; never hallucinated.
        assert result.status in ("confirmed", "fuzzy"), result
        if result.status == "fuzzy":
            assert result.ratio >= FUZZY_THRESHOLD

    def test_query_text_takes_precedence_over_snippet(self, tmp_path):
        _write(tmp_path, "Foo.java", "SELECT 1")
        entity = _make_entity(query_text="SELECT 1",
                              code_snippet="entirely unrelated")
        result = verify_entity(entity, [tmp_path])
        assert result.matched_field == "query_text"
        assert result.status == "confirmed"


# ── Mode B: subagent verdict parser ──────────────────────────────────────────

class TestSubagentVerdictParser:
    def test_parse_yes(self):
        v = parse_verdict('{"result": "YES", "reason": "exact match"}')
        assert v.result == "YES"
        assert v.reason == "exact match"

    def test_parse_no(self):
        v = parse_verdict('{"result": "NO", "reason": "not present"}')
        assert v.result == "NO"

    def test_parse_partial(self):
        v = parse_verdict('{"result": "PARTIAL", "reason": "paraphrased"}')
        assert v.result == "PARTIAL"

    def test_lowercase_result_normalised(self):
        v = parse_verdict('{"result": "yes", "reason": ""}')
        assert v.result == "YES"

    def test_markdown_fences_stripped(self):
        v = parse_verdict('```json\n{"result": "NO", "reason": "x"}\n```')
        assert v.result == "NO"

    def test_non_json_fallback_finds_verdict(self):
        v = parse_verdict("NO -- the claim is not in source")
        assert v.result == "NO"

    def test_partial_preferred_over_yes_in_fallback(self):
        # If the model emits free-form prose containing multiple verdicts,
        # PARTIAL wins over YES — fail safe.
        v = parse_verdict("PARTIAL match, not a clean YES")
        assert v.result == "PARTIAL"

    def test_unknown_result_becomes_partial(self):
        v = parse_verdict('{"result": "MAYBE", "reason": "x"}')
        assert v.result == "PARTIAL"


# ── Mode C: self-correction trigger criteria ─────────────────────────────────

class TestSelfCorrectionTriggers:
    def test_database_query_is_high_stakes(self):
        assert is_high_stakes(_make_entity(entity_type="DatabaseQuery",
                                           query_text="SELECT 1"))

    def test_external_service_is_high_stakes(self):
        assert is_high_stakes(_make_entity(entity_type="ExternalService"))

    def test_function_with_query_text_is_high_stakes(self):
        assert is_high_stakes(_make_entity(query_text="SELECT 1"))

    def test_function_without_query_text_is_not_high_stakes(self):
        assert not is_high_stakes(_make_entity(entity_type="Function"))

    def test_low_confidence_does_not_fire(self):
        entity = _make_entity(entity_type="DatabaseQuery",
                              query_text="SELECT 1", confidence=0.7)
        assert not should_self_correct(entity, verifier_said_no=True)

    def test_no_fire_on_partial_verdict(self):
        entity = _make_entity(entity_type="DatabaseQuery",
                              query_text="SELECT 1", confidence=0.95)
        assert not should_self_correct(entity, verifier_said_no=False)

    def test_fires_when_all_criteria_met(self):
        entity = _make_entity(entity_type="DatabaseQuery",
                              query_text="SELECT 1", confidence=0.9)
        assert should_self_correct(entity, verifier_said_no=True)


# ── VerifierLoop orchestration ───────────────────────────────────────────────

class TestVerifierLoop:
    async def test_confirmed_entity_marked_confirmed(self, tmp_path):
        _write(tmp_path, "Foo.java", 'String q = "SELECT * FROM users";')
        entity = _make_entity(query_text="SELECT * FROM users")
        loop = VerifierLoop()
        out, stats = await loop.run([entity], source_roots=[tmp_path])
        assert out[0].verified == "confirmed"
        assert out[0].verifier_mode == "deterministic"
        assert stats.confirmed == 1
        assert stats.subagent_calls == 0

    async def test_hallucinated_entity_dropped_without_subagent(self, tmp_path):
        # When sub-agent is disabled, hallucinated Mode-A verdicts stick.
        _write(tmp_path, "Foo.java", "// nothing relevant here")
        entity = _make_entity(
            entity_type="DatabaseQuery",
            query_text="SELECT * FROM nonexistent_table WHERE x = 1",
        )
        loop = VerifierLoop(enable_subagent=False)
        out, stats = await loop.run([entity], source_roots=[tmp_path])
        assert out[0].verified == "hallucinated"
        assert stats.hallucinated == 1
        assert stats.confirmed == 0
        assert stats.subagent_calls == 0

    async def test_subagent_upgrades_fuzzy_to_confirmed(self, tmp_path):
        source = "competitorsService.getPayerCompetitors(payerId, tier)"
        _write(tmp_path, "Foo.java", f"void run() {{ {source}; }}")
        entity = _make_entity(
            code_snippet="competitorsService.getPayerCompetitors(payer_id, tier)",
        )
        loop = VerifierLoop()
        # Force the agent to say YES even though Mode A might say fuzzy.
        loop._agent.verify = AsyncMock(
            return_value=SubagentVerdict("YES", "matches modulo case"),
        )
        out, _stats = await loop.run([entity], source_roots=[tmp_path])
        assert out[0].verified == "confirmed"

    async def test_subagent_no_with_low_confidence_skips_mode_c(self, tmp_path):
        _write(tmp_path, "Foo.java", "// nothing matches")
        entity = _make_entity(
            entity_type="DatabaseQuery",
            query_text="SELECT * FROM ghost_table",
            confidence=0.7,   # ← below the 0.8 floor
        )
        loop = VerifierLoop()
        loop._agent.verify = AsyncMock(
            return_value=SubagentVerdict("NO", "not in source"),
        )
        loop._corrector.recorrect = AsyncMock(
            side_effect=AssertionError("Mode C must not fire below floor"),
        )
        out, stats = await loop.run([entity], source_roots=[tmp_path])
        assert out[0].verified == "hallucinated"
        assert stats.self_correction_fires == 0

    async def test_self_correction_fires_on_high_confidence_dispute(self, tmp_path):
        # Source contains the corrected query, not the original (hallucinated) one.
        good_query = "SELECT id FROM payers WHERE active = 1"
        _write(tmp_path, "Foo.java",
               f'String q = "{good_query}"; repo.exec(q);')
        entity = _make_entity(
            entity_type="DatabaseQuery",
            query_text="SELECT * FROM nonexistent_table",
            confidence=0.95,
        )
        loop = VerifierLoop()
        # Mode B: dispute.
        loop._agent.verify = AsyncMock(
            return_value=SubagentVerdict("NO", "not in source"),
        )
        # Mode C: returns the corrected query.
        loop._corrector.recorrect = AsyncMock(
            return_value=SelfCorrectionResult(
                accepted=True,
                new_query_text=good_query,
                new_code_snippet="repo.exec(q);",
                new_confidence=0.92,
                notes="found the real query",
            ),
        )
        out, stats = await loop.run([entity], source_roots=[tmp_path])
        assert stats.self_correction_fires == 1
        assert stats.self_correction_accepted == 1
        assert out[0].query_text == good_query
        assert out[0].verified == "confirmed"
        assert out[0].verifier_mode == "self_correction"

    async def test_self_correction_still_conflicting_marks_conflicting(self, tmp_path):
        _write(tmp_path, "Foo.java", "// nothing matches")
        entity = _make_entity(
            entity_type="DatabaseQuery",
            query_text="SELECT * FROM ghost_table",
            confidence=0.95,
        )
        loop = VerifierLoop()
        loop._agent.verify = AsyncMock(
            return_value=SubagentVerdict("NO", "not in source"),
        )
        loop._corrector.recorrect = AsyncMock(
            return_value=SelfCorrectionResult(
                accepted=False,
                still_conflicting=True,
                notes="retry also disputed by verifier",
            ),
        )
        out, stats = await loop.run([entity], source_roots=[tmp_path])
        assert out[0].verified == "conflicting"
        assert stats.conflicting == 1
        assert stats.self_correction_fires == 1
        assert stats.self_correction_accepted == 0

    async def test_empty_input_returns_empty_stats(self):
        loop = VerifierLoop()
        out, stats = await loop.run([], source_roots=[Path("/tmp")])
        assert out == []
        assert stats.total == 0


# ── /query verified-filter ───────────────────────────────────────────────────

class _FakeHit:
    """Minimal stand-in for HybridSearcher result, mirroring its surface."""

    def __init__(self, urn: str, verified: str = "skipped"):
        self.urn = urn
        self.payload = {"qualified_name": urn.split(":")[-1],
                        "entity_type": "Function",
                        "verified": verified}


class TestQueryFilter:
    def test_filter_keeps_confirmed_and_fuzzy(self):
        from companybrain.api.routes.query import _filter_verified
        hits = [
            _FakeHit("urn:a", "confirmed"),
            _FakeHit("urn:b", "fuzzy"),
            _FakeHit("urn:c", "hallucinated"),
            _FakeHit("urn:d", "conflicting"),
        ]
        kept = _filter_verified(hits, include_unverified=False)
        urns = {h.urn for h in kept}
        assert urns == {"urn:a", "urn:b"}

    def test_filter_keeps_skipped_for_backwards_compat(self):
        # Pre-V16 payloads have no verified key → defaults to "skipped".
        from companybrain.api.routes.query import _filter_verified
        hits = [_FakeHit("urn:x", "skipped")]
        kept = _filter_verified(hits, include_unverified=False)
        assert len(kept) == 1

    def test_include_unverified_passes_everything_through(self):
        from companybrain.api.routes.query import _filter_verified
        hits = [
            _FakeHit("urn:a", "confirmed"),
            _FakeHit("urn:b", "hallucinated"),
            _FakeHit("urn:c", "conflicting"),
        ]
        kept = _filter_verified(hits, include_unverified=True)
        assert len(kept) == 3
