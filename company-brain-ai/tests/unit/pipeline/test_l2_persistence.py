import json
from pathlib import Path

from companybrain.pipeline.context_hierarchy import L2SharedContext
from companybrain.pipeline.shared_context_accumulator import L2Persistence


def test_save_and_load_round_trip(tmp_path: Path):
    l2 = L2SharedContext(
        domain_glossary={"NIQ": "Network IQ — competitiveness scoring"},
        service_registry={"PaymentService": {"role": "service", "file": "src/p.ts"}},
        pattern_library=["SAGA in PaymentService"],
        field_semantics={"niq_score": "0-100 competitiveness rank"},
    )
    L2Persistence.save(l2, tmp_path, "main")
    out = L2Persistence.load(tmp_path, "main")
    assert out.domain_glossary == l2.domain_glossary
    assert out.service_registry == l2.service_registry
    assert out.pattern_library == l2.pattern_library


def test_load_missing_file_returns_empty(tmp_path):
    out = L2Persistence.load(tmp_path, "main")
    assert out.is_empty()


def test_load_corrupt_file_returns_empty(tmp_path):
    p = L2Persistence.cache_path(tmp_path, "main")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("{ this is not json")
    out = L2Persistence.load(tmp_path, "main")
    assert out.is_empty()


def test_branch_segregation(tmp_path):
    l2_main = L2SharedContext(domain_glossary={"X": "main"})
    l2_feat = L2SharedContext(domain_glossary={"X": "feature"})
    L2Persistence.save(l2_main, tmp_path, "main")
    L2Persistence.save(l2_feat, tmp_path, "feature/foo")
    assert L2Persistence.load(tmp_path, "main").domain_glossary["X"] == "main"
    assert L2Persistence.load(tmp_path, "feature/foo").domain_glossary["X"] == "feature"


def test_unsafe_branch_chars_are_sanitised(tmp_path):
    l2 = L2SharedContext(domain_glossary={"X": "Y"})
    L2Persistence.save(l2, tmp_path, "feat/with/slashes")
    expected = tmp_path / ".brain" / ".l2-cache" / "feat_with_slashes.json"
    assert expected.exists()


def test_version_mismatch_returns_empty(tmp_path):
    p = L2Persistence.cache_path(tmp_path, "main")
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"version": 99, "domain_glossary": {"X": "Y"}}))
    out = L2Persistence.load(tmp_path, "main")
    assert out.is_empty()


def test_save_creates_parent_dirs(tmp_path):
    l2 = L2SharedContext(domain_glossary={"A": "B"})
    L2Persistence.save(l2, tmp_path, "main")
    cache_file = L2Persistence.cache_path(tmp_path, "main")
    assert cache_file.exists()
    data = json.loads(cache_file.read_text())
    assert data["version"] == 1
    assert data["domain_glossary"] == {"A": "B"}


def test_field_semantics_round_trip(tmp_path):
    l2 = L2SharedContext(field_semantics={"niq_score": "0-100 rank"})
    L2Persistence.save(l2, tmp_path, "develop")
    out = L2Persistence.load(tmp_path, "develop")
    assert out.field_semantics == {"niq_score": "0-100 rank"}
