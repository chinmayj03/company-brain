"""
Acceptance tests for ADR-0057 — Universal File Extraction.

The ADR's original wording references ``run_pipeline_harness`` + ``brain_query``
(end-to-end Neo4j-backed queries). Those depend on Java-side persistence of the
new entity types, which is owned by a follow-up PR — see the Phase 1 scope in
docs/adrs/ADR-0057-universal-file-extraction.md.

What this suite DOES verify, end-to-end:
  1. FileWalker.walk_universal() classifies every fixture file to the right
     extractor kind.
  2. The dispatch + extractors produce the expected entities for the network-iq
     fixture (Dockerfile → ContainerImage with openjdk; pom.xml → Spring + pg
     dependencies; application.yml → ConfigKey with semantic_tag database_url
     pointing at a postgres URL; ReportingUtilsTest.java → routed as code).
  3. The universal_extraction Stage 0.5b function aggregates these into the
     telemetry shape the orchestrator stores.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from companybrain.extractors.dispatch import get_extractor
from companybrain.models.entities import PipelineStartRequest, RepoConfig, RepoType
from companybrain.pipeline.file_walker import FileWalker
from companybrain.pipeline.universal_extraction import run_universal_extraction


FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "network-iq-snapshot"


def _read_extract(path: Path):
    extractor = get_extractor(path)
    assert extractor is not None, f"no extractor claims {path}"
    return extractor.extract(path, path.read_text(encoding="utf-8"), repo="network-iq")


# ── walker classification ─────────────────────────────────────────────────────

def test_walker_classifies_every_fixture_file():
    walker = FileWalker(repo_root=FIXTURE, respect_gitignore=False)
    kinds = {info.relative_path: info.extractor_kind for info in walker.walk_universal()}

    assert kinds["Dockerfile"] == "infra"
    assert kinds["docker-compose.yml"] == "infra"
    assert kinds["pom.xml"] == "manifest"
    assert kinds["src/main/resources/application.yml"] == "config"
    assert kinds[".github/workflows/ci.yml"] == "ci"
    assert kinds["README.md"] == "doc"
    assert kinds["src/test/java/com/example/reporting/ReportingUtilsTest.java"] == "code"


# ── per-extractor assertions against the fixture ──────────────────────────────

def test_dockerfile_extracts_openjdk_base_image():
    batch = _read_extract(FIXTURE / "Dockerfile")
    image_names = [i.name for i in batch.container_images]
    assert any("openjdk" in name.lower() for name in image_names)
    # Multi-stage: first stage has alias "build"
    assert any(i.stage_alias == "build" for i in batch.container_images)
    # EXPOSE 8080 is on the runtime stage
    assert any(8080 in s.exposed_ports for s in batch.runtime_stages)


def test_pom_extracts_spring_and_postgresql_deps():
    batch = _read_extract(FIXTURE / "pom.xml")
    names = {d.name for d in batch.dependencies}
    assert "org.springframework.boot:spring-boot-starter-web" in names
    assert "org.postgresql:postgresql" in names
    pg = next(d for d in batch.dependencies if d.name == "org.postgresql:postgresql")
    assert pg.version == "42.6.0"
    assert pg.scope == "runtime"
    assert pg.ecosystem == "maven"


def test_application_yml_has_database_url_semantic_tag():
    batch = _read_extract(FIXTURE / "src" / "main" / "resources" / "application.yml")
    db_keys = [k for k in batch.config_keys if k.semantic_tag == "database_url"]
    assert db_keys, "expected at least one ConfigKey with semantic_tag=database_url"
    assert any("postgresql" in k.value for k in db_keys)


def test_compose_extracts_app_and_postgres_services():
    batch = _read_extract(FIXTURE / "docker-compose.yml")
    by_name = {s.name: s for s in batch.service_defs}
    assert "app" in by_name and "db" in by_name
    assert by_name["db"].image == "postgres:15"
    assert "db" in by_name["app"].depends_on


def test_ci_workflow_extracts_jobs_and_triggers():
    batch = _read_extract(FIXTURE / ".github" / "workflows" / "ci.yml")
    assert batch.workflow_jobs, "expected at least one WorkflowJob"
    job = batch.workflow_jobs[0]
    assert job.ci_system == "github"
    assert "push" in job.triggers and "pull_request" in job.triggers


def test_readme_extracts_top_heading():
    batch = _read_extract(FIXTURE / "README.md")
    doc = batch.documentation[0]
    assert doc.title == "network-iq-snapshot"
    assert "network-iq-snapshot" in doc.headings


def test_test_file_method_docstrings_via_javadoc():
    """Javadoc extractor pulls @return tags from the test class."""
    from companybrain.extractors.javadoc_extractor import JavadocExtractor
    path = FIXTURE / "src" / "test" / "java" / "com" / "example" / "reporting" / "ReportingUtilsTest.java"
    batch = JavadocExtractor().extract(path, path.read_text(encoding="utf-8"), repo="network-iq")
    summaries = [d.summary for d in batch.method_docs]
    assert any("filter should keep records" in s.lower() for s in summaries)


# ── Stage 0.5b aggregation ────────────────────────────────────────────────────

def test_universal_extraction_stage_aggregates_counts():
    """The Stage 0.5b orchestrator hook returns a counts-and-entities summary."""
    request = PipelineStartRequest(
        endpoint_path="/dummy",
        http_method="GET",
        repos=[RepoConfig(local_path=str(FIXTURE), type=RepoType.BACKEND)],
        workspace_id="test-ws",
    )

    async def _noop_progress(*args, **kwargs):
        return None

    summary = asyncio.run(run_universal_extraction(
        request=request, progress=_noop_progress,
    ))

    assert summary["files"] >= 5            # docker, compose, pom, yml, ci, README → 6
    assert summary["entities"] >= 10        # rough lower bound
    # Each expected kind appears at least once
    for kind in ("infra", "manifest", "config", "ci", "doc"):
        assert summary["by_kind"].get(kind, 0) >= 1, f"missing kind={kind} in {summary['by_kind']}"


# ── ADR-0057 acceptance criteria from the original ADR (parity check) ─────────
# The original ADR lists 5 acceptance tests that depend on run_pipeline_harness
# / brain_query. Those are deferred to the follow-up PR that wires persistence
# into Neo4j. The structural equivalents below assert the same SHAPES at the
# extractor layer — i.e. "if persistence is correct, these queries would work".

def test_adr_acceptance_dockerfile_extracted_shape():
    batch = _read_extract(FIXTURE / "Dockerfile")
    assert any("openjdk" in i.name.lower() for i in batch.container_images)


def test_adr_acceptance_database_url_semantic_tag_shape():
    batch = _read_extract(FIXTURE / "src" / "main" / "resources" / "application.yml")
    db_configs = [k for k in batch.config_keys if k.semantic_tag == "database_url"]
    assert len(db_configs) >= 1
    assert any("postgresql" in c.value.lower() for c in db_configs)


def test_adr_acceptance_pom_dependencies_extracted_shape():
    batch = _read_extract(FIXTURE / "pom.xml")
    names = {d.name for d in batch.dependencies}
    assert any(n.endswith("spring-boot-starter-web") for n in names)
    assert any(n.endswith("postgresql") for n in names)


@pytest.mark.skip(reason="BehavioralSpec extraction is LLM-bound — owned by ADR-0057 Phase 2 (test_spec_extractor)")
def test_adr_acceptance_test_as_spec_extracted_for_lob():
    pass


@pytest.mark.skip(reason="end-to-end brain_query is owned by ADR-0057 Phase 2 (Neo4j persistence + query routing)")
def test_adr_acceptance_query_what_database_after_universal_extraction():
    pass
