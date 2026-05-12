# ADR-0057 — Universal File Extraction (every extractable file in the repo, not just source)

**Status:** Proposed
**Date:** 2026-05-11
**Builds on:** existing chunker (ADR-0044/47) which only handles `*.java/*.py/*.ts`
**Sequenced with:** ADR-0055/56/58/59/60 — six-ADR set, parallel-shippable.

---

## Context

The user's framing: *"it shouldn't just be focused on .py/.java etc files but any extractable file in the repo."*

The benchmark proved this: questions about deployment (Dockerfile), config (application.yml), CI (.github/workflows/), database driver version (pom.xml), feature flags (.env / yml), build tooling (build.gradle), READMEs, ADRs, OpenAPI specs, Postman collections, and inline Javadoc/comments are ALL invisible to the brain today. That's roughly 30% of the questions in the benchmark — not nuance, structural absence.

Tree-sitter has grammars for ~150 languages including YAML, TOML, Markdown, Dockerfile, JSON, HCL (Terraform), Thrift, Proto, GraphQL, and many config formats. We're using ~3 of them.

**This ADR is the file-walker generalisation, not just docs.** It expands the brain to extract:

- **Documentation**: `README.md`, `CHANGELOG.md`, `*.adoc`, `docs/**/*.md`, in-repo ADRs (`docs/adrs/*.md`), the `BRAIN.md` per-repo memory file (already in scope via ADR-0051 P3 but not extracted).
- **Code-adjacent docs**: Javadoc / docstrings / JSDoc inside source files. These are extracted today as part of `code_snippet` but never structured as their own entities.
- **Config**: `application*.yml`, `*.properties`, `*.toml`, `.env*`, `*.json` config files (excluding `package.json` / `tsconfig.json` which build tooling owns).
- **Build & dependency manifests**: `pom.xml`, `build.gradle*`, `package.json`, `Cargo.toml`, `go.mod`, `requirements.txt`, `pyproject.toml`, `Pipfile`.
- **Infra-as-code**: `Dockerfile*`, `docker-compose*.yml`, `*.tf` (Terraform), `*.yaml` (Kubernetes manifests), `Procfile`, `Makefile*`.
- **CI/CD**: `.github/workflows/*.yml`, `.gitlab-ci.yml`, `bitbucket-pipelines.yml`, `circleci/config.yml`, `Jenkinsfile`.
- **API & schema specs** — *defer to ADR-0058* (OpenAPI, GraphQL, Proto, DDL — they need their own structured extractor; this ADR catches the FILE existence and basic key-value, the deep extraction belongs to 0058).
- **Test-as-spec**: tests are extracted today but treated as code. They're also a SPECIFICATION ("the method should do X under condition Y"). Add a `BehavioralSpec` entity type derived from test method bodies.

---

## Decision

Three coordinated changes:

### D1 — Generalise the file walker

`pipeline/file_walker.py` currently classifies files into `extractable`, `oversized`, `large`, `lockfile`, `generated`. Today only files in `_CODE_EXTS` (`.java/.py/.ts/...`) actually flow through extraction. **Change**: drop the language gate; instead, dispatch by extension to one of N typed extractors.

```python
# pipeline/file_walker.py — augmented
_EXTRACTOR_DISPATCH = {
    # source code (existing)
    ".java": "code", ".py": "code", ".ts": "code", ".tsx": "code",
    ".js": "code", ".jsx": "code", ".kt": "code", ".go": "code",
    # docs (NEW)
    ".md":  "doc", ".adoc": "doc", ".rst": "doc", ".txt": "doc",
    # config (NEW)
    ".yml": "config", ".yaml": "config", ".toml": "config",
    ".properties": "config", ".env": "config",
    # build manifests (NEW)
    ".xml": "manifest_xml",  # POM
    "package.json": "manifest_npm",
    "Cargo.toml": "manifest_cargo",
    "go.mod": "manifest_go",
    "requirements.txt": "manifest_pip",
    "pyproject.toml": "manifest_pip_toml",
    # infra (NEW)
    "Dockerfile": "infra_docker",
    "docker-compose.yml": "infra_compose",
    ".tf": "infra_terraform",
    "Makefile": "infra_make",
    # CI (NEW)
    ".github/workflows/*.yml": "ci_github",
    ".gitlab-ci.yml": "ci_gitlab",
    "Jenkinsfile": "ci_jenkins",
    # schema (NEW; ADR-0058 owns the deep extraction)
    ".sql": "schema_sql",
    ".proto": "schema_proto",
    ".graphql": "schema_graphql",
    ".graphqls": "schema_graphql",
    ".avsc": "schema_avro",
    "openapi.yaml": "schema_openapi", "openapi.yml": "schema_openapi",
    "swagger.yaml": "schema_openapi", "swagger.yml": "schema_openapi",
}
```

### D2 — Per-extractor entity emitters (one Python module per type)

Each extractor type produces structured entities + edges. The base contract:

```python
class Extractor(Protocol):
    def supports(self, path: Path) -> bool: ...
    def extract(self, path: Path, content: str) -> ExtractedBatch: ...
```

Output entities by type (most are deterministic; only Markdown extraction calls the LLM):

| Extractor | Entity types emitted | Edges emitted | LLM use |
|---|---|---|---|
| `doc` (Markdown/AsciiDoc) | `Documentation`, `Heading`, `CodeBlock` | `DOCUMENTS`, `EXAMPLES` | Optional: summarise long docs |
| `config` (YAML/TOML/properties) | `ConfigKey { path, value, file, semantic_tag }` | `CONFIGURES` | None (deterministic) |
| `manifest_xml` (POM) | `Dependency { groupId, artifactId, version, scope }`, `BuildPlugin` | `DEPENDS_ON_LIBRARY` | None |
| `manifest_npm` | `Dependency { name, version, dev_only }` | `DEPENDS_ON_LIBRARY` | None |
| `infra_docker` | `ContainerImage`, `RuntimeStage` | `BASED_ON`, `EXPOSES_PORT`, `RUNS_COMMAND` | None |
| `infra_compose` | `ServiceDefinition { name, image, env, ports }` | `DEPLOYS`, `LINKS_TO` | None |
| `ci_github` | `WorkflowJob { name, triggers, runs_on, steps }` | `RUNS_ON_PR`, `RUNS_ON_PUSH` | None |
| Test-as-spec (within `code` extractor) | `BehavioralSpec { specifies_method, given, when, then }` | `SPECIFIES` | LLM (one batch per test class) |
| Javadoc inside source | `MethodDoc { method_urn, summary, params, returns, throws }` | `DOCUMENTS` | None (parser-based) |

### D3 — Semantic tagging for ConfigKey

Raw config keys (`spring.datasource.url`) are not useful by themselves. Add a tagger that maps known patterns to semantic tags:

```python
_CONFIG_SEMANTIC_TAGS = {
    re.compile(r"datasource\.url$|database_url$|DB_URL$"): "database_url",
    re.compile(r"datasource\.username$"):                   "database_credential",
    re.compile(r"redis\.host$|REDIS_URL$"):                 "cache_url",
    re.compile(r"\.api\.key$|_API_KEY$|secret"):            "secret",
    re.compile(r"feature\..*\.enabled$|FEATURE_"):          "feature_flag",
    re.compile(r"oauth|jwt|saml"):                          "auth_config",
    re.compile(r"sentry|datadog|newrelic"):                 "observability",
    re.compile(r"port$|PORT$"):                             "port",
    re.compile(r"replicas$|min_instances$|max_instances$"): "scaling",
    # ... extensible via plugins (ADR-0052 P6) for org-specific tags
}
```

This makes `ConfigKey { path: "spring.datasource.url", semantic_tag: "database_url" }` discoverable for the question "what database is this?".

---

## File ownership for THIS PR (parallel-safe)

```
company-brain-ai/src/companybrain/extractors/                       # NEW DIRECTORY
company-brain-ai/src/companybrain/extractors/__init__.py
company-brain-ai/src/companybrain/extractors/base.py
company-brain-ai/src/companybrain/extractors/doc_extractor.py
company-brain-ai/src/companybrain/extractors/config_extractor.py
company-brain-ai/src/companybrain/extractors/manifest_extractor.py
company-brain-ai/src/companybrain/extractors/infra_extractor.py
company-brain-ai/src/companybrain/extractors/ci_extractor.py
company-brain-ai/src/companybrain/extractors/javadoc_extractor.py
company-brain-ai/src/companybrain/extractors/test_spec_extractor.py
company-brain-ai/src/companybrain/extractors/dispatch.py            # extension → extractor router
company-brain-ai/src/companybrain/extractors/semantic_tags.py
tests/unit/test_universal_extraction.py                              # NEW
tests/acceptance/test_universal_extraction_network_iq.py             # NEW
```

Append-only edits to:

```
company-brain-ai/src/companybrain/pipeline/file_walker.py    # augment _EXTRACTOR_DISPATCH; route via dispatcher
company-brain-ai/src/companybrain/pipeline/orchestrator.py   # invoke universal extractors alongside code chunker
company-brain-ai/src/companybrain/models/entities.py         # add Documentation, ConfigKey, Dependency,
                                                              # ContainerImage, ServiceDefinition, WorkflowJob,
                                                              # BehavioralSpec, MethodDoc; new edge consts
```

Does NOT touch any code chunker, ContextAgent, or files owned by ADR-0055/56/58/59/60. Schema-deep-extraction (`.sql`, `.proto`, OpenAPI) is owned by ADR-0058.

---

## Acceptance test

```python
async def test_dockerfile_extracted():
    await run_pipeline_harness(repo="fixtures/network-iq-snapshot")
    images = await brain_query("list all ContainerImage entities")
    assert any("openjdk" in i.name.lower() for i in images)


async def test_database_url_semantic_tag():
    await run_pipeline_harness(repo="fixtures/network-iq-snapshot")
    db_configs = await brain_query("ConfigKey where semantic_tag = 'database_url'")
    assert len(db_configs) >= 1
    assert any("postgresql" in c.value.lower() for c in db_configs)


async def test_pom_dependencies_extracted():
    await run_pipeline_harness(repo="fixtures/network-iq-snapshot")
    deps = await brain_query("list all Dependency entities")
    assert any(d.name == "spring-boot-starter-web" for d in deps)
    assert any(d.name == "postgresql" for d in deps)


async def test_test_as_spec_extracted_for_lob():
    """ReportingUtilsTest.java has a test asserting LOB filter behaviour;
    the BehavioralSpec entity should capture the GIVEN/WHEN/THEN structure."""
    await run_pipeline_harness(repo="fixtures/network-iq-snapshot")
    specs = await brain_query("BehavioralSpec where specifies_method contains 'Reporting'")
    assert len(specs) >= 3
    assert any("LOB" in s.then for s in specs)


async def test_query_what_database_after_universal_extraction():
    """The benchmark question C9 should now PASS."""
    await run_pipeline_harness(repo="fixtures/network-iq-snapshot")
    answer = await brain_query("what database does this codebase use? what version?")
    assert "postgres" in answer.lower()
    assert any(re.search(r"\d+\.\d+", answer))   # version number present
```

---

## Effort estimate

3 days. Most extractors are ≤50 LOC each (YAML/TOML are stdlib parsers; XML is `xml.etree`; Markdown is `markdown-it-py` or regex). Test-as-spec extractor is the LLM one (~$0.001 per test class).

---

## Action items

1. [ ] Augment `file_walker.py` with `_EXTRACTOR_DISPATCH` map.
2. [ ] Implement 8 extractor modules under `extractors/`.
3. [ ] Implement `semantic_tags.py` regex catalog.
4. [ ] Append new entity types + edge constants to `models/entities.py`.
5. [ ] Wire dispatch into `orchestrator.py` — universal extractors run in Stage 0.5 (alongside structural pre-pass).
6. [ ] Acceptance: 5 tests above all pass; the benchmark C7/C8/C9/C11 questions move from FAIL to PASS.
7. [ ] Telemetry: per-run count of entities by extractor type.
