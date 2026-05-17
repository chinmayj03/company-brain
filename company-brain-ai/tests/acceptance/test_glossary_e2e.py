"""
Acceptance tests for Glossary Auto-Discovery (A1.6).

All tests run without a live LLM — definitions are generated via a mock
provider that returns a fixed definition string.

Scenario:
  - 25 synthetic brain entities all mention "PriorAuth" across code + SQL types
  - GlossaryDiscoverer finds "PriorAuth" with occurrences ≥ 20
  - GlossaryPromoter promotes it and saves to WorkspaceTuningStore (tmp dir)
  - get_active_glossary() returns the promoted term
  - format_glossary_block() produces prompt-ready output containing "PriorAuth"
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from companybrain.workspace.glossary.discoverer import GlossaryDiscoverer
from companybrain.workspace.glossary.promoter import GlossaryPromoter
from companybrain.workspace.glossary.loader import format_glossary_block, get_glossary_context
from companybrain.workspace.tuning_store import WorkspaceTuningStore


# ── Helpers ───────────────────────────────────────────────────────────────────

WORKSPACE_ID = "ws-test-001"


def _make_entity(
    qualified_name: str,
    summary: str,
    entity_type: str = "component",
    file: str = "src/service.py",
    meta: dict | None = None,
) -> dict:
    return {
        "id": f"urn:cb:test:test:repo:{entity_type}:{qualified_name}",
        "entity_type": entity_type,
        "repo": "repo",
        "file": file,
        "qualified_name": qualified_name,
        "t1_summary": summary,
        "metadata": meta or {},
    }


def _build_corpus() -> list[dict]:
    """Build 25+ synthetic entities that all mention PriorAuth in ≥2 source types."""
    entities = []

    # 15 Python/code entities
    for i in range(15):
        entities.append(_make_entity(
            qualified_name=f"PriorAuthService.method_{i}",
            summary=f"Validates PriorAuth eligibility for claim {i}",
            entity_type="component",
            file=f"src/prior_auth/service_{i}.py",
        ))

    # 10 SQL/data_model entities
    for i in range(10):
        entities.append(_make_entity(
            qualified_name=f"prior_auth_table_{i}",
            summary=f"Stores PriorAuth records with status and effective date for member {i}",
            entity_type="data_model",
            file=f"schema/prior_auth_{i}.sql",
        ))

    return entities


def _mock_llm_provider():
    """Build a mock async LLM provider that returns a fixed definition."""
    provider = MagicMock()
    mock_response = MagicMock()
    mock_response.content = "A PriorAuth is a healthcare authorization required before a service is rendered."
    provider.chat = AsyncMock(return_value=mock_response)
    return provider


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_store(tmp_path: Path) -> WorkspaceTuningStore:
    return WorkspaceTuningStore(tmp_path)


@pytest.fixture
def corpus() -> list[dict]:
    return _build_corpus()


@pytest.fixture
def discoverer() -> GlossaryDiscoverer:
    # Low thresholds so tests are self-consistent; e2e sets real thresholds
    return GlossaryDiscoverer(min_occurrences=1, min_source_types=1)


# ── Discovery tests ───────────────────────────────────────────────────────────

class TestDiscoveryE2E:
    def test_prior_auth_discovered(self, discoverer, corpus):
        candidates = discoverer.discover(corpus)
        terms = [c.term for c in candidates]
        assert "PriorAuth" in terms, f"PriorAuth not found in {terms[:10]}"

    def test_prior_auth_occurrences_gte_20(self, discoverer, corpus):
        candidates = discoverer.discover(corpus)
        pa = next(c for c in candidates if c.term == "PriorAuth")
        assert pa.occurrences >= 20, f"Expected ≥20 occurrences, got {pa.occurrences}"

    def test_prior_auth_spans_multiple_source_types(self, discoverer, corpus):
        candidates = discoverer.discover(corpus)
        pa = next(c for c in candidates if c.term == "PriorAuth")
        # Corpus has code + sql entities
        assert len(pa.source_types) >= 2, f"source_types={pa.source_types}"

    def test_prior_auth_has_contexts(self, discoverer, corpus):
        candidates = discoverer.discover(corpus)
        pa = next(c for c in candidates if c.term == "PriorAuth")
        assert pa.contexts, "Expected at least one context sentence"
        assert any("PriorAuth" in ctx for ctx in pa.contexts)

    def test_candidates_sorted_descending(self, discoverer, corpus):
        candidates = discoverer.discover(corpus)
        counts = [c.occurrences for c in candidates]
        assert counts == sorted(counts, reverse=True)


# ── Promotion tests ───────────────────────────────────────────────────────────

class TestPromotionE2E:
    def test_promotes_prior_auth_with_mock_llm(self, discoverer, corpus, tmp_store):
        candidates = discoverer.discover(corpus)
        # cluster aliases
        candidates = discoverer.cluster_aliases(candidates)

        llm = _mock_llm_provider()
        promoter = GlossaryPromoter(store=tmp_store, llm_provider=llm)
        promoted, rejected = promoter.promote_candidates(
            WORKSPACE_ID,
            candidates,
            min_occurrences=20,
            min_source_types=2,
        )

        assert len(promoted) >= 1
        pa = next((c for c in promoted if c.term == "PriorAuth"), None)
        assert pa is not None, "PriorAuth not in promoted list"
        assert pa.promoted is True

    def test_definition_filled_by_mock_llm(self, discoverer, corpus, tmp_store):
        candidates = discoverer.discover(corpus)
        candidates = discoverer.cluster_aliases(candidates)

        llm = _mock_llm_provider()
        promoter = GlossaryPromoter(store=tmp_store, llm_provider=llm)
        promoted, _ = promoter.promote_candidates(
            WORKSPACE_ID,
            candidates,
            min_occurrences=20,
            min_source_types=2,
        )

        pa = next((c for c in promoted if c.term == "PriorAuth"), None)
        assert pa is not None
        # Definition should have been filled by the mock LLM
        assert pa.definition != ""
        assert "PriorAuth" in pa.definition or "authorization" in pa.definition.lower()

    def test_saves_to_tuning_store(self, discoverer, corpus, tmp_store):
        candidates = discoverer.discover(corpus)
        candidates = discoverer.cluster_aliases(candidates)

        llm = _mock_llm_provider()
        promoter = GlossaryPromoter(store=tmp_store, llm_provider=llm)
        promoter.promote_candidates(
            WORKSPACE_ID,
            candidates,
            min_occurrences=20,
            min_source_types=2,
        )

        stored = tmp_store.get(WORKSPACE_ID, "glossary", [])
        assert stored, "Nothing saved to tuning store"
        stored_terms = [t["term"] for t in stored]
        assert "PriorAuth" in stored_terms

    def test_get_active_glossary_returns_promoted(self, discoverer, corpus, tmp_store):
        candidates = discoverer.discover(corpus)
        candidates = discoverer.cluster_aliases(candidates)

        llm = _mock_llm_provider()
        promoter = GlossaryPromoter(store=tmp_store, llm_provider=llm)
        promoter.promote_candidates(
            WORKSPACE_ID,
            candidates,
            min_occurrences=20,
            min_source_types=2,
        )

        glossary = promoter.get_active_glossary(WORKSPACE_ID)
        assert glossary
        assert any(t["term"] == "PriorAuth" for t in glossary)

    def test_rejected_terms_below_threshold(self, discoverer, corpus, tmp_store):
        """Terms appearing rarely should land in the rejected list."""
        # Build candidates that include one rare term
        rare_entity = _make_entity(
            qualified_name="ObscureRareTermOnce",
            summary="ObscureRareTerm is mentioned once in this entity only",
            entity_type="component",
        )
        candidates = discoverer.discover(corpus + [rare_entity])
        candidates = discoverer.cluster_aliases(candidates)

        promoter = GlossaryPromoter(store=tmp_store, llm_provider=None)
        _, rejected = promoter.promote_candidates(
            WORKSPACE_ID,
            candidates,
            min_occurrences=20,
            min_source_types=2,
        )

        rejected_terms = [c.term for c in rejected]
        assert "ObscureRareTerm" in rejected_terms or len(rejected) > 0

    def test_no_definition_when_llm_absent(self, discoverer, corpus, tmp_store):
        candidates = discoverer.discover(corpus)
        candidates = discoverer.cluster_aliases(candidates)

        promoter = GlossaryPromoter(store=tmp_store, llm_provider=None)
        promoted, _ = promoter.promote_candidates(
            WORKSPACE_ID,
            candidates,
            min_occurrences=20,
            min_source_types=2,
        )

        pa = next((c for c in promoted if c.term == "PriorAuth"), None)
        assert pa is not None
        # No LLM → definition should be empty string (graceful skip)
        assert pa.definition == ""


# ── Loader tests ──────────────────────────────────────────────────────────────

class TestLoaderE2E:
    def _seed_store(self, store: WorkspaceTuningStore) -> None:
        store.set(WORKSPACE_ID, "glossary", [
            {
                "term": "PriorAuth",
                "aliases": ["prior_auth", "PA"],
                "definition": "Authorization required before a healthcare service is rendered.",
                "occurrences": 42,
                "source_types": ["code", "sql"],
            },
            {
                "term": "BenefitAccumulator",
                "aliases": [],
                "definition": "Tracks accumulated healthcare benefits for a member.",
                "occurrences": 25,
                "source_types": ["code"],
            },
        ])

    def test_format_block_contains_prior_auth(self, tmp_store):
        self._seed_store(tmp_store)
        terms = tmp_store.get(WORKSPACE_ID, "glossary", [])
        block = format_glossary_block(terms)
        assert "PriorAuth" in block

    def test_format_block_contains_aliases(self, tmp_store):
        self._seed_store(tmp_store)
        terms = tmp_store.get(WORKSPACE_ID, "glossary", [])
        block = format_glossary_block(terms)
        assert "prior_auth" in block or "PA" in block

    def test_format_block_contains_definition(self, tmp_store):
        self._seed_store(tmp_store)
        terms = tmp_store.get(WORKSPACE_ID, "glossary", [])
        block = format_glossary_block(terms)
        assert "Authorization" in block or "authorization" in block

    def test_format_block_has_header(self, tmp_store):
        self._seed_store(tmp_store)
        terms = tmp_store.get(WORKSPACE_ID, "glossary", [])
        block = format_glossary_block(terms)
        assert "## Domain Glossary" in block

    def test_format_block_empty_returns_empty_string(self):
        assert format_glossary_block([]) == ""

    def test_format_block_respects_max_terms(self, tmp_store):
        terms = [
            {"term": f"Term{i}", "aliases": [], "definition": f"Definition {i}",
             "occurrences": 30 - i, "source_types": ["code"]}
            for i in range(30)
        ]
        block = format_glossary_block(terms, max_terms=5)
        # Only first 5 terms included
        assert "Term0" in block
        assert "Term29" not in block

    def test_get_glossary_context_integration(self, tmp_store):
        self._seed_store(tmp_store)
        context = get_glossary_context(WORKSPACE_ID, tmp_store, max_terms=20)
        assert "PriorAuth" in context
        assert "## Domain Glossary" in context

    def test_get_glossary_context_empty_workspace(self, tmp_store):
        context = get_glossary_context("nonexistent-ws", tmp_store, max_terms=20)
        assert context == ""


# ── Alias clustering E2E ──────────────────────────────────────────────────────

class TestAliasClustering:
    def test_prior_auth_forms_clustered(self):
        """End-to-end: corpus with PriorAuth + prior_auth → single entry post-cluster."""
        entities = []
        for i in range(15):
            entities.append(_make_entity(
                qualified_name=f"PriorAuthService_{i}",
                summary=f"PriorAuth eligibility check {i}",
                entity_type="component",
                file="src/service.py",
            ))
        for i in range(10):
            entities.append(_make_entity(
                qualified_name=f"prior_auth_table_{i}",
                summary=f"prior_auth record for member {i}",
                entity_type="data_model",
                file="schema.sql",
            ))

        discoverer = GlossaryDiscoverer(min_occurrences=1, min_source_types=1)
        candidates = discoverer.discover(entities)
        pre_cluster_terms = [c.term for c in candidates]

        # Both PriorAuth and prior_auth might appear
        has_both = "PriorAuth" in pre_cluster_terms and "prior_auth" in pre_cluster_terms
        merged = discoverer.cluster_aliases(candidates)
        post_cluster_terms = [c.term for c in merged]

        if has_both:
            # After clustering, there should be only one representative
            prior_auth_count = sum(
                1 for t in post_cluster_terms
                if t in {"PriorAuth", "prior_auth"}
            )
            assert prior_auth_count == 1, (
                f"Expected exactly 1 representative for PriorAuth forms, got {prior_auth_count}: {post_cluster_terms}"
            )
        else:
            # At minimum PriorAuth should be present
            assert "PriorAuth" in post_cluster_terms or "prior_auth" in post_cluster_terms
