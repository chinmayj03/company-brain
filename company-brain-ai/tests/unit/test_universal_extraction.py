"""Unit tests for ADR-0057 universal file extractors."""
from __future__ import annotations

from pathlib import Path

import pytest

from companybrain.extractors.ci_extractor import CIExtractor
from companybrain.extractors.config_extractor import ConfigExtractor
from companybrain.extractors.dispatch import extractor_kind_for, get_extractor
from companybrain.extractors.doc_extractor import DocExtractor
from companybrain.extractors.infra_extractor import InfraExtractor
from companybrain.extractors.javadoc_extractor import JavadocExtractor
from companybrain.extractors.manifest_extractor import ManifestExtractor
from companybrain.extractors.semantic_tags import tag_config_path
from companybrain.extractors.test_spec_extractor import TestSpecExtractor


# ── dispatch ──────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,expected_kind", [
    ("Dockerfile",                "infra"),
    ("Dockerfile.prod",           "infra"),
    ("docker-compose.yml",        "infra"),
    ("pom.xml",                   "manifest"),
    ("package.json",              "manifest"),
    ("requirements.txt",          "manifest"),
    ("Cargo.toml",                "manifest"),
    ("go.mod",                    "manifest"),
    ("pyproject.toml",            "manifest"),
    ("application.yml",           "config"),
    (".env",                      "config"),
    (".env.production",           "config"),
    ("app.properties",            "config"),
    ("README.md",                 "doc"),
    ("docs/onboarding.adoc",      "doc"),
    (".github/workflows/ci.yml",  "ci"),
    (".gitlab-ci.yml",            "ci"),
    ("Jenkinsfile",               "ci"),
])
def test_dispatch_routes_path_to_kind(path, expected_kind):
    assert extractor_kind_for(Path(path)) == expected_kind


def test_dispatch_returns_none_for_unknown():
    assert extractor_kind_for(Path("foo.bin")) is None
    assert extractor_kind_for(Path("image.png")) is None


# ── semantic tags ─────────────────────────────────────────────────────────────

@pytest.mark.parametrize("path,tag", [
    ("spring.datasource.url",          "database_url"),
    ("DATABASE_URL",                   "database_url"),
    ("DB_URL",                         "database_url"),
    ("spring.datasource.username",     "database_credential"),
    ("DB_PASSWORD",                    "database_credential"),
    ("redis.host",                     "cache_url"),
    ("REDIS_URL",                      "cache_url"),
    ("stripe.api.key",                 "secret"),
    ("MY_API_KEY",                     "secret"),
    ("feature.darkmode.enabled",       "feature_flag"),
    ("FEATURE_NEW_CHECKOUT",           "feature_flag"),
    ("server.port",                    "port"),
    ("server.host",                    "host"),
    ("autoscale.max_instances",        "scaling"),
    ("logging.level.root",             "logging"),
    ("sentry.dsn",                     "observability"),
])
def test_semantic_tag_lookup(path, tag):
    assert tag_config_path(path) == tag


def test_semantic_tag_unknown_returns_none():
    assert tag_config_path("totally.unrelated.key") is None
    assert tag_config_path("") is None


# ── doc extractor ─────────────────────────────────────────────────────────────

def test_doc_extracts_headings_and_code_blocks():
    md = "# Top\n\nIntro paragraph.\n\n## Section\n\n```python\nprint('hi')\n```\n"
    batch = DocExtractor().extract(Path("README.md"), md, repo="r")
    doc = batch.documentation[0]
    assert doc.title == "Top"
    assert doc.headings == ["Top", "Section"]
    assert len(doc.code_blocks) == 1
    assert "print('hi')" in doc.code_blocks[0]


def test_doc_title_falls_back_to_stem_when_no_heading():
    batch = DocExtractor().extract(Path("notes.md"), "no heading here\n", repo="r")
    assert batch.documentation[0].title == "notes"


# ── config extractor ──────────────────────────────────────────────────────────

def test_config_yaml_flattens_and_tags():
    yml = (
        "spring:\n"
        "  datasource:\n"
        "    url: jdbc:postgresql://db/foo\n"
        "    username: app\n"
        "server:\n"
        "  port: 8080\n"
    )
    batch = ConfigExtractor().extract(Path("application.yml"), yml, repo="r")
    keys = {k.path: k for k in batch.config_keys}
    assert keys["spring.datasource.url"].semantic_tag == "database_url"
    assert keys["spring.datasource.url"].value == "jdbc:postgresql://db/foo"
    assert keys["spring.datasource.username"].semantic_tag == "database_credential"
    assert keys["server.port"].semantic_tag == "port"


def test_config_env_parses_and_skips_comments():
    env = "DB_URL=postgresql://localhost/foo\n# comment\nexport FEATURE_X=1\nAPI_KEY=\"sk-xxx\"\n"
    batch = ConfigExtractor().extract(Path(".env"), env, repo="r")
    keys = {k.path: k for k in batch.config_keys}
    assert keys["DB_URL"].value == "postgresql://localhost/foo"
    assert keys["DB_URL"].semantic_tag == "database_url"
    assert keys["FEATURE_X"].semantic_tag == "feature_flag"
    assert keys["API_KEY"].value == "sk-xxx"   # quotes stripped
    assert keys["API_KEY"].semantic_tag == "secret"


def test_config_properties_parses_pairs():
    props = "spring.datasource.url=jdbc:postgresql://db/foo\n!banged\nlogging.level.root=INFO\n"
    batch = ConfigExtractor().extract(Path("app.properties"), props, repo="r")
    paths = {k.path for k in batch.config_keys}
    assert "spring.datasource.url" in paths
    assert "logging.level.root" in paths


def test_config_does_not_claim_package_json():
    """package.json belongs to the manifest extractor, not config."""
    assert not ConfigExtractor().supports(Path("package.json"))


def test_config_malformed_yaml_returns_empty_batch():
    bad = "spring:\n  - missing: :::\n  invalid"
    batch = ConfigExtractor().extract(Path("bad.yml"), bad, repo="r")
    # No crash; possibly empty
    assert batch.extractor_kind == "config"


# ── manifest extractor ────────────────────────────────────────────────────────

def test_manifest_pom_extracts_deps_and_plugins():
    pom = """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <dependencies>
    <dependency>
      <groupId>org.springframework.boot</groupId>
      <artifactId>spring-boot-starter-web</artifactId>
      <version>3.1.0</version>
    </dependency>
    <dependency>
      <groupId>org.postgresql</groupId>
      <artifactId>postgresql</artifactId>
      <version>42.6.0</version>
      <scope>runtime</scope>
    </dependency>
  </dependencies>
  <build>
    <plugins>
      <plugin>
        <groupId>org.springframework.boot</groupId>
        <artifactId>spring-boot-maven-plugin</artifactId>
      </plugin>
    </plugins>
  </build>
</project>"""
    batch = ManifestExtractor().extract(Path("pom.xml"), pom, repo="r")
    names = {d.name: d for d in batch.dependencies}
    assert "org.springframework.boot:spring-boot-starter-web" in names
    assert names["org.springframework.boot:spring-boot-starter-web"].version == "3.1.0"
    assert names["org.springframework.boot:spring-boot-starter-web"].ecosystem == "maven"
    assert names["org.postgresql:postgresql"].scope == "runtime"
    plugin_names = {p.name for p in batch.build_plugins}
    assert "org.springframework.boot:spring-boot-maven-plugin" in plugin_names


def test_manifest_npm_extracts_dev_and_runtime():
    pkg = '{"dependencies":{"react":"^18.0.0"},"devDependencies":{"vitest":"^1.0.0"}}'
    batch = ManifestExtractor().extract(Path("package.json"), pkg, repo="r")
    by_name = {d.name: d for d in batch.dependencies}
    assert by_name["react"].scope == "runtime"
    assert by_name["react"].ecosystem == "npm"
    assert by_name["vitest"].scope == "dev"


def test_manifest_requirements_txt():
    req = "fastapi>=0.115.0\n# a comment\npydantic\nuvicorn[standard]==0.30.1\n"
    batch = ManifestExtractor().extract(Path("requirements.txt"), req, repo="r")
    names = {d.name for d in batch.dependencies}
    assert {"fastapi", "pydantic", "uvicorn"} <= names


def test_manifest_pyproject_pep621_and_poetry():
    toml = """
[project]
name = "x"
dependencies = ["fastapi>=0.115", "pydantic"]

[tool.poetry.dependencies]
requests = "^2.0"
"""
    batch = ManifestExtractor().extract(Path("pyproject.toml"), toml, repo="r")
    names = {d.name for d in batch.dependencies}
    assert {"fastapi", "pydantic", "requests"} <= names


# ── infra extractor ───────────────────────────────────────────────────────────

def test_infra_dockerfile_multistage():
    df = (
        "FROM openjdk:17-jdk-slim AS build\n"
        "WORKDIR /app\n"
        "RUN ./mvnw package\n"
        "\n"
        "FROM openjdk:17-jre-slim\n"
        "EXPOSE 8080\n"
        "ENTRYPOINT [\"java\",\"-jar\",\"/app/app.jar\"]\n"
    )
    batch = InfraExtractor().extract(Path("Dockerfile"), df, repo="r")
    image_names = [i.name for i in batch.container_images]
    assert image_names == ["openjdk:17-jdk-slim", "openjdk:17-jre-slim"]
    aliases = [i.stage_alias for i in batch.container_images]
    assert aliases == ["build", None]
    second_stage = batch.runtime_stages[1]
    assert 8080 in second_stage.exposed_ports
    assert "java" in (second_stage.entrypoint or "")


def test_infra_compose_service_defs():
    compose = (
        "services:\n"
        "  app:\n"
        "    image: myapp:latest\n"
        "    ports: ['8080:8080']\n"
        "    depends_on: [db]\n"
        "    environment:\n"
        "      SPRING_DATASOURCE_URL: jdbc:postgresql://db/foo\n"
        "  db:\n"
        "    image: postgres:15\n"
    )
    batch = InfraExtractor().extract(Path("docker-compose.yml"), compose, repo="r")
    by_name = {s.name: s for s in batch.service_defs}
    assert by_name["app"].image == "myapp:latest"
    assert by_name["app"].depends_on == ["db"]
    assert by_name["app"].env["SPRING_DATASOURCE_URL"].startswith("jdbc:postgresql")
    assert by_name["db"].image == "postgres:15"


# ── CI extractor ──────────────────────────────────────────────────────────────

def test_ci_github_actions_jobs_and_triggers():
    gh = (
        "name: CI\n"
        "on:\n"
        "  push:\n"
        "    branches: [main]\n"
        "  pull_request: {}\n"
        "jobs:\n"
        "  test:\n"
        "    runs-on: ubuntu-latest\n"
        "    steps:\n"
        "      - uses: actions/checkout@v4\n"
        "      - name: Run tests\n"
        "        run: ./mvnw test\n"
    )
    batch = CIExtractor().extract(Path(".github/workflows/ci.yml"), gh, repo="r")
    job = batch.workflow_jobs[0]
    assert job.ci_system == "github"
    assert "push" in job.triggers and "pull_request" in job.triggers
    assert job.runs_on == "ubuntu-latest"
    assert any("checkout" in s for s in job.steps)


def test_ci_jenkinsfile_extracts_stages():
    jenkins = """
pipeline {
  agent any
  stages {
    stage('Build') { steps { sh 'mvn build' } }
    stage('Deploy') { steps { sh 'kubectl apply' } }
  }
}
"""
    batch = CIExtractor().extract(Path("Jenkinsfile"), jenkins, repo="r")
    names = [j.name for j in batch.workflow_jobs]
    assert "Build" in names and "Deploy" in names


# ── javadoc extractor ─────────────────────────────────────────────────────────

def test_javadoc_java_parses_tags():
    java = """
/**
 * Persists the user record.
 *
 * @param user the user to persist
 * @return id of the saved row
 * @throws DuplicateUserException when email already exists
 */
public long saveUser(User user) { return repo.save(user); }
"""
    batch = JavadocExtractor().extract(Path("UserSvc.java"), java, repo="r")
    doc = batch.method_docs[0]
    assert doc.summary == "Persists the user record."
    assert doc.params == {"user": "the user to persist"}
    assert doc.returns == "id of the saved row"
    assert doc.throws == {"DuplicateUserException": "when email already exists"}


def test_javadoc_python_docstrings():
    py = '''
def add(a, b):
    """Sum two integers."""
    return a + b

async def fetch_user(uid):
    """Fetch user by id."""
    return await db.fetch(uid)
'''
    batch = JavadocExtractor().extract(Path("util.py"), py, repo="r")
    summaries = {d.method_urn.split("::")[-1]: d.summary for d in batch.method_docs}
    assert summaries["add"] == "Sum two integers."
    assert summaries["fetch_user"] == "Fetch user by id."


# ── test_spec extractor (stub) ────────────────────────────────────────────────

def test_test_spec_supports_typical_test_files():
    ext = TestSpecExtractor()
    # supports() returns True for files clearly named as tests
    assert ext.supports(Path("UserServiceTest.java"))
    assert ext.supports(Path("user.spec.ts"))
    assert ext.supports(Path("tests/test_user.py"))


def test_test_spec_extract_is_empty_stub():
    ext = TestSpecExtractor()
    batch = ext.extract(Path("FooTest.java"), "class FooTest {}", repo="r")
    assert batch.entity_count == 0
    assert batch.extractor_kind == "test_spec"


# ── ExtractedBatch ────────────────────────────────────────────────────────────

def test_extracted_batch_entity_count():
    from companybrain.models.entities import (
        ConfigKey,
        ContainerImage,
        Dependency,
        ExtractedBatch,
    )
    b = ExtractedBatch(
        file="x", repo="r", extractor_kind="mixed",
        config_keys=[ConfigKey(file="x", repo="r", path="a", value="1")],
        dependencies=[Dependency(file="x", repo="r", name="d"),
                      Dependency(file="x", repo="r", name="e")],
        container_images=[ContainerImage(file="x", repo="r", name="img")],
    )
    assert b.entity_count == 4
