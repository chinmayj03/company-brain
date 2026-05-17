"""
Unit tests for GlossaryDiscoverer.

All tests are self-contained — no live LLM required.
"""

from __future__ import annotations

import pytest

from companybrain.workspace.glossary.discoverer import (
    GlossaryCandidate,
    GlossaryDiscoverer,
    _normalize,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def discoverer() -> GlossaryDiscoverer:
    return GlossaryDiscoverer(min_occurrences=2, min_source_types=1)


def _make_entity(
    qualified_name: str = "",
    summary: str = "",
    entity_type: str = "component",
    file: str = "src/foo.py",
    meta: dict | None = None,
) -> dict:
    return {
        "id": "urn:cb:test:test:repo:component:foo",
        "entity_type": entity_type,
        "repo": "repo",
        "file": file,
        "qualified_name": qualified_name,
        "t1_summary": summary,
        "metadata": meta or {},
    }


# ── _extract_terms tests ──────────────────────────────────────────────────────

class TestExtractTerms:
    def test_extracts_pascal_case(self, discoverer):
        terms = list(discoverer._extract_terms("PriorAuth eligibility check"))
        assert "PriorAuth" in terms

    def test_extracts_all_caps(self, discoverer):
        terms = list(discoverer._extract_terms("HIPAA compliance required"))
        assert "HIPAA" in terms

    def test_extracts_title_case_phrase(self, discoverer):
        terms = list(discoverer._extract_terms("Prior Authorization required for claim"))
        assert "Prior Authorization" in terms

    def test_no_terms_in_plain_lowercase(self, discoverer):
        terms = list(discoverer._extract_terms("this is plain lowercase text"))
        assert terms == []

    def test_multiple_pascal_case(self, discoverer):
        terms = list(discoverer._extract_terms("ClaimSubmission and BenefitAccumulator"))
        assert "ClaimSubmission" in terms
        assert "BenefitAccumulator" in terms

    def test_short_caps_ignored(self, discoverer):
        terms = list(discoverer._extract_terms("We called the ID endpoint"))
        # "ID" is in _COMMON_ACRONYMS, should not appear
        assert "ID" not in terms


# ── _is_stopterm tests ────────────────────────────────────────────────────────

class TestIsStopterm:
    def test_stop_term_string(self, discoverer):
        assert discoverer._is_stopterm("String") is True

    def test_stop_term_error(self, discoverer):
        assert discoverer._is_stopterm("Error") is True

    def test_stop_term_return(self, discoverer):
        assert discoverer._is_stopterm("Return") is True

    def test_domain_term_not_stop(self, discoverer):
        assert discoverer._is_stopterm("PriorAuth") is False

    def test_domain_term_hipaa(self, discoverer):
        assert discoverer._is_stopterm("HIPAA") is False

    def test_short_term_is_stop(self, discoverer):
        assert discoverer._is_stopterm("AB") is True

    def test_stop_term_plural_strings(self, discoverer):
        assert discoverer._is_stopterm("Strings") is True


# ── discover() tests ──────────────────────────────────────────────────────────

class TestDiscover:
    def test_discovers_repeated_pascal_case(self, discoverer):
        entities = [
            _make_entity(
                qualified_name="PriorAuthService",
                summary="Handles PriorAuth eligibility verification",
            ),
            _make_entity(
                qualified_name="PriorAuthRepository",
                summary="Persists PriorAuth records to database",
                file="src/repo.py",
            ),
        ]
        candidates = discoverer.discover(entities)
        terms = [c.term for c in candidates]
        assert "PriorAuth" in terms

    def test_occurrences_counted(self, discoverer):
        entities = [
            _make_entity(summary="PriorAuth check required"),
            _make_entity(summary="PriorAuth validation passed"),
            _make_entity(summary="PriorAuth status updated"),
        ]
        candidates = discoverer.discover(entities)
        pa = next((c for c in candidates if c.term == "PriorAuth"), None)
        assert pa is not None
        assert pa.occurrences >= 3

    def test_source_types_tracked(self, discoverer):
        entities = [
            _make_entity(
                summary="PriorAuth eligibility",
                entity_type="component",
                file="src/service.py",
            ),
            _make_entity(
                summary="PriorAuth table join",
                entity_type="data_model",
                file="schema.sql",
            ),
        ]
        candidates = discoverer.discover(entities)
        pa = next((c for c in candidates if c.term == "PriorAuth"), None)
        assert pa is not None
        assert len(pa.source_types) >= 2

    def test_contexts_populated(self, discoverer):
        entities = [
            _make_entity(summary="PriorAuth eligibility check required"),
            _make_entity(summary="PriorAuth status must be verified"),
        ]
        candidates = discoverer.discover(entities)
        pa = next((c for c in candidates if c.term == "PriorAuth"), None)
        assert pa is not None
        assert len(pa.contexts) > 0
        assert any("PriorAuth" in ctx for ctx in pa.contexts)

    def test_sorted_by_occurrences(self, discoverer):
        entities = [
            _make_entity(summary="BenefitAccumulator check"),
            _make_entity(summary="PriorAuth check"),
            _make_entity(summary="PriorAuth validation"),
            _make_entity(summary="PriorAuth update"),
        ]
        candidates = discoverer.discover(entities)
        if len(candidates) >= 2:
            # First candidate should have more occurrences than the last
            assert candidates[0].occurrences >= candidates[-1].occurrences

    def test_empty_entities_returns_empty(self, discoverer):
        assert discoverer.discover([]) == []

    def test_stop_terms_not_in_candidates(self, discoverer):
        entities = [
            _make_entity(summary="String processing for data handling"),
            _make_entity(summary="String concatenation and error handling"),
        ]
        candidates = discoverer.discover(entities)
        terms = [c.term for c in candidates]
        assert "String" not in terms
        assert "Error" not in terms


# ── cluster_aliases tests ─────────────────────────────────────────────────────

class TestClusterAliases:
    def _make_candidate(self, term: str, occurrences: int = 5) -> GlossaryCandidate:
        return GlossaryCandidate(
            term=term,
            normalized=_normalize(term),
            occurrences=occurrences,
            source_types={"code"},
            contexts=[f"example sentence with {term}"],
            aliases=[],
        )

    def test_merges_pascal_and_snake_case(self, discoverer):
        candidates = [
            self._make_candidate("PriorAuth", occurrences=15),
            self._make_candidate("prior_auth", occurrences=8),
        ]
        merged = discoverer.cluster_aliases(candidates)
        # Should collapse to one entry
        assert len(merged) == 1
        canonical = merged[0]
        assert canonical.term in {"PriorAuth", "prior_auth"}
        # Other form should be in aliases
        other = "prior_auth" if canonical.term == "PriorAuth" else "PriorAuth"
        assert other in canonical.aliases

    def test_merges_three_forms(self, discoverer):
        candidates = [
            self._make_candidate("PriorAuth", occurrences=20),
            self._make_candidate("prior_auth", occurrences=10),
            self._make_candidate("priorauth", occurrences=5),
        ]
        merged = discoverer.cluster_aliases(candidates)
        assert len(merged) == 1
        canonical = merged[0]
        assert canonical.term == "PriorAuth"  # highest occurrences
        assert len(canonical.aliases) == 2

    def test_distinct_terms_not_merged(self, discoverer):
        candidates = [
            self._make_candidate("PriorAuth", occurrences=10),
            self._make_candidate("BenefitAccumulator", occurrences=8),
        ]
        merged = discoverer.cluster_aliases(candidates)
        assert len(merged) == 2

    def test_merged_occurrences_summed(self, discoverer):
        candidates = [
            self._make_candidate("PriorAuth", occurrences=15),
            self._make_candidate("prior_auth", occurrences=8),
        ]
        merged = discoverer.cluster_aliases(candidates)
        assert merged[0].occurrences == 23

    def test_merged_source_types_combined(self, discoverer):
        c1 = self._make_candidate("PriorAuth")
        c1.source_types = {"code"}
        c2 = self._make_candidate("prior_auth")
        c2.source_types = {"sql"}
        merged = discoverer.cluster_aliases([c1, c2])
        assert "code" in merged[0].source_types
        assert "sql" in merged[0].source_types

    def test_single_candidate_unchanged(self, discoverer):
        candidates = [self._make_candidate("PriorAuth", occurrences=30)]
        merged = discoverer.cluster_aliases(candidates)
        assert len(merged) == 1
        assert merged[0].term == "PriorAuth"
        assert merged[0].aliases == []


# ── normalize helper ──────────────────────────────────────────────────────────

class TestNormalize:
    def test_pascal_case(self):
        assert _normalize("PriorAuth") == "priorauth"

    def test_snake_case(self):
        assert _normalize("prior_auth") == "priorauth"

    def test_title_case_phrase(self):
        assert _normalize("Prior Auth") == "priorauth"

    def test_all_caps(self):
        assert _normalize("PRIOR_AUTH") == "priorauth"
