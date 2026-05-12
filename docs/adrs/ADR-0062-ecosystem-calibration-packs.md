# ADR-0062 — Ecosystem Calibration Packs (the universality fix for ADR-0057/0058/0060)

**Status:** Proposed
**Date:** 2026-05-11
**Builds on:** ADR-0051 P3 (skills detection + per-repo SKILL.md), ADR-0057 (universal extraction), ADR-0058 (schema awareness), ADR-0060 (BC v2 + few-shot)
**Sequenced with:** ships AFTER 0057/0058/0060 land with their Java-Spring defaults; this ADR generalises them.

---

## Context

The benchmark + cross-ADR audit revealed a clean line: **the brain's architecture is language-agnostic, but its calibration is Java/Spring monocultural**. Specifically:

- **ADR-0057** (universal extraction) ships a semantic-tag catalog flavoured for Spring property names (`spring.datasource.url`, `redis.host`). Doesn't know Pydantic-Settings, Django settings, NestJS ConfigService, or 12-factor `DATABASE_URL` style.
- **ADR-0058** (schema awareness) ships `jooq_binding.py` — a Java-only ORM binding extractor. Python uses SQLAlchemy/Tortoise; TypeScript uses Drizzle/Prisma/TypeORM; Go uses sqlc/GORM.
- **ADR-0060** (BC v2) ships 30 few-shot examples leaning Java/Spring (`@PreAuthorize`, `@Transactional`, JPA repository patterns). FastAPI/NestJS/Django/Go projects need different examples.

This is exactly the problem Claude Code's Skills system solves: framework expertise lives in a `SKILL.md` file loaded on demand. ADR-0051 P3 already builds the primitive (`harness/skills.py::detect_framework`). This ADR extends it from "load one SKILL.md" to "load a full calibration pack" containing semantic tags, ORM bindings, doc-format parsers, test-framework parsers, and few-shot libraries.

**The architectural shape after this ADR**:

```
   ┌─────────────────────────────────────────────┐
   │  Agnostic shells (ADR-0057/0058/0060)       │
   │  — universal file walker                    │
   │  — schema extractor framework               │
   │  — BC v2 schema with 28 typed fields        │
   │  — extraction loop                          │
   └─────────────────┬───────────────────────────┘
                     │ delegates per-decision to:
                     ▼
   ┌─────────────────────────────────────────────┐
   │  Ecosystem Calibration Pack (this ADR)      │
   │  — one pack per (language, framework)       │
   │  — auto-loaded from frameworks/<id>/        │
   │  — overridable + extensible by customers    │
   └─────────────────────────────────────────────┘
```

The shells contain ZERO ecosystem assumptions. All "what does X look like in this codebase" decisions live in the pack.

---

## Decision

A `frameworks/<lang>-<framework>/` directory pattern. Each pack ships 7 files with a stable contract. The brain detects the primary framework (per ADR-0051 P3) and loads the matching pack. Multiple packs CAN be loaded for polyglot repos (e.g., Spring backend + Next.js frontend → load both packs, dispatch per-file).

### D1 — Pack contract (the 7 files every pack must ship)

```
frameworks/<id>/                              # e.g. frameworks/java-spring/
├── pack.yaml                                  # manifest — required
├── SKILL.md                                   # ADR-0051 P3 — agent system prompt fragment
├── semantic_tags.py                           # ADR-0057 — config-key regex catalog
├── orm_bindings.py                            # ADR-0058 — schema-to-code resolver
├── doc_format.py                              # ADR-0057 — docstring/comment parser
├── test_framework.py                          # ADR-0057 — test-as-spec parser
├── few_shot_library.py                        # ADR-0060 — 8-12 BC v2 worked examples
├── known_idioms.json                          # ADR-0055 — pre-seeded patterns to detect
└── README.md                                  # human-readable: when this pack is right
```

**`pack.yaml`** — declarative manifest:

```yaml
id: java-spring
name: "Java + Spring Boot"
version: 1.0.0
language: java
framework: spring-boot
detection:
  any_of:
    - file_glob: "**/*.java"
      content_pattern: "@SpringBootApplication"
    - file_glob: "pom.xml"
      content_pattern: "spring-boot-starter"
    - file_glob: "build.gradle*"
      content_pattern: "org.springframework.boot"
    - file_glob: "**/application*.yml"
provides:
  semantic_tags: semantic_tags.py
  orm_bindings: orm_bindings.py
  doc_format: doc_format.py
  test_framework: test_framework.py
  few_shot_library: few_shot_library.py
  known_idioms: known_idioms.json
extends: ~          # or another pack id; "java-base" → spring inherits Java basics
priority: 100       # tiebreaker when multiple packs match
```

**`semantic_tags.py`** — overrides the default catalog from ADR-0057:

```python
# Each pack's tags supplement (not replace) the universal defaults
SEMANTIC_TAGS = [
    (r"^spring\.datasource\.url$",          "database_url"),
    (r"^spring\.datasource\.username$",     "database_credential"),
    (r"^spring\.redis\.host$",              "cache_url"),
    (r"^spring\.security\..*",              "auth_config"),
    (r"^management\.metrics\..*",           "observability"),
    (r"^logging\.level\..*",                "logging_config"),
    (r"^spring\.profiles\.active$",         "deployment_profile"),
    # ... 30+ Spring-specific patterns ...
]
```

**`orm_bindings.py`** — declares which extractor handles ORM bindings:

```python
from companybrain.extractors.orm_binding.jooq import JooqBindingExtractor
from companybrain.extractors.orm_binding.spring_jpa import SpringJpaBindingExtractor

EXTRACTORS = [
    JooqBindingExtractor,        # if target/generated-sources/jooq exists
    SpringJpaBindingExtractor,   # for @Entity classes
]
```

**`doc_format.py`** — declares docstring/comment parser:

```python
from companybrain.extractors.doc.javadoc import JavadocParser

DOC_PARSER = JavadocParser
INLINE_COMMENT_PATTERN = r"//\s*(.+)$"
BLOCK_COMMENT_PATTERN = r"/\*\*?(.+?)\*/"
```

**`test_framework.py`** — declares test parser:

```python
from companybrain.extractors.test.junit import JUnitTestParser

TEST_PARSER = JUnitTestParser
TEST_FILE_PATTERNS = ["**/*Test.java", "**/Test*.java", "**/*IT.java"]
ANNOTATION_TEST_METHOD = "@Test"
ANNOTATION_DESCRIBE = "@DisplayName"   # JUnit 5
```

**`few_shot_library.py`** — 8-12 BC v2 examples specific to this ecosystem:

```python
# Each example is (entity_input_xml, expected_BusinessContext_v2_json)
EXAMPLES = [
    {
        "input": '<method qname="UserController.getUser">@GetMapping("/users/{id}") public User getUser(@PathVariable Long id) { return userService.findById(id); }</method>',
        "expected": {
            "purpose": "HTTP GET endpoint to fetch a user by ID",
            "is_idempotent": True,
            "transaction_mode": "read_only",
            "security_class": "authenticated",
            "performance_class": "O(1)",
            "null_handling": {"id": "checked"},     # @PathVariable validates non-null
            "anti_patterns": [],
            "engineering_notes": ["Spring auto-converts path variable to Long"],
            # ...
        }
    },
    # ... 7-11 more, each ~250 tokens ...
]
```

**`known_idioms.json`** — pre-seeded patterns ADR-0055 should auto-detect:

```json
{
  "idioms": [
    {
      "name": "defensive_filter_copy",
      "shape": "X.prototype(req)",
      "intent": "Defensive copy before mutating filters",
      "antipattern_when_violated": true
    },
    {
      "name": "soft_delete_filter",
      "shape": "X.is_current.eq(true)",
      "intent": "Read only current rows; soft delete pattern",
      "antipattern_when_violated": false
    },
    {
      "name": "constructor_injection",
      "shape": "@RequiredArgsConstructor + final fields",
      "intent": "Lombok-generated constructor injection (vs @Autowired field injection)"
    }
  ]
}
```

### D2 — Pack loader

`harness/skills.py::detect_framework` already returns ONE framework. Extend to return a list of all matching packs (a polyglot monorepo could have 2-3). The loader composes them:

```python
def load_packs(repo_path: Path) -> CalibrationContext:
    matched = detect_all_matching_packs(repo_path)
    matched.sort(key=lambda p: p.manifest.priority, reverse=True)
    ctx = CalibrationContext()
    for pack in matched:
        ctx.merge(pack)              # higher priority wins on conflicts
    return ctx
```

`CalibrationContext` exposes `.semantic_tags`, `.orm_extractors`, `.doc_parser`, `.test_parser`, `.few_shot_examples`, `.known_idioms` — the agnostic shells call these.

### D3 — Shells delegate, never hard-code

Refactor ADR-0057/0058/0060 to delegate every per-ecosystem decision:

**ADR-0057 — `extractors/config_extractor.py`**:
```python
# BEFORE (Java-flavoured):
SEMANTIC_TAGS = [(r"spring\.datasource\..*", "database_url"), ...]

# AFTER (delegates to pack):
def get_semantic_tag(key: str, ctx: CalibrationContext) -> Optional[str]:
    for pattern, tag in ctx.semantic_tags:
        if re.match(pattern, key):
            return tag
    return None
```

**ADR-0058 — `extractors/dispatch.py`**:
```python
# BEFORE: hardcoded jooq_binding for *Tables.java
# AFTER: ctx.orm_extractors decides which to invoke for each generated file
```

**ADR-0060 — `pipeline/business_context_v2_prompt.py`**:
```python
# BEFORE: from few_shot_library import EXAMPLES (Java-only)
# AFTER:
def build_prompt(ctx: CalibrationContext) -> str:
    examples = ctx.few_shot_examples           # pulled from active packs
    return TEMPLATE.format(examples=examples)
```

After D3, the shells contain ZERO regex catalogs, ZERO ORM-specific code, ZERO few-shot examples baked into Python. Everything is pack-supplied.

---

## D4 — Five packs to ship initially

### Pack 1 — `java-spring` (build first; matches the existing demo target)

- Detection: `@SpringBootApplication` OR `spring-boot-starter` in pom.xml
- ORM bindings: jOOQ (existing from ADR-0058), Spring Data JPA `@Entity`
- Doc format: Javadoc parser
- Test framework: JUnit 5 (Jupiter)
- Few-shot: 10 examples covering `@RestController`, `@Service`, `@Repository`, JPA query methods, jOOQ DSL, `@Transactional`, `@PreAuthorize`, validation, exception handlers, async `@Async` methods
- Known idioms: defensive filter copy, soft-delete filter, Lombok constructor injection, Spring profile gating

### Pack 2 — `python-fastapi`

- Detection: `from fastapi import` OR `fastapi` in pyproject.toml/requirements.txt
- ORM bindings: SQLAlchemy 2.x ORM, Tortoise ORM, raw asyncpg
- Doc format: Python docstrings (Google / NumPy style)
- Test framework: pytest (`pytest-asyncio` aware)
- Few-shot: 10 examples covering FastAPI routes, Pydantic models, dependency injection (`Depends`), async DB sessions, OAuth2 / JWT auth, BackgroundTasks, exception handlers, middleware
- Known idioms: dependency injection, Pydantic validation, async/await patterns, context manager DB sessions

### Pack 3 — `typescript-nestjs`

- Detection: `@nestjs/core` in package.json OR `@Controller` decorators
- ORM bindings: TypeORM, Prisma, Drizzle
- Doc format: JSDoc / TSDoc parser
- Test framework: Jest with `@nestjs/testing`
- Few-shot: 10 examples covering `@Controller`, `@Injectable`, `@Module`, providers, guards, interceptors, pipes, exception filters, DTOs with `class-validator`, async services
- Known idioms: dependency injection via constructor, decorator-driven validation, RxJS observables in services

### Pack 4 — `python-django`

- Detection: `manage.py` OR `django` in requirements
- ORM bindings: Django ORM (Model classes, QuerySets)
- Doc format: Python docstrings
- Test framework: Django TestCase + pytest-django
- Few-shot: 10 examples covering `View` / `APIView` (DRF), `Model` declarations, `QuerySet` methods, signals, middleware, custom managers, serializers, permissions, migrations
- Known idioms: model managers, signal handlers, middleware chain, `select_related` / `prefetch_related` for N+1 avoidance

### Pack 5 — `go-chi` (or `go-stdlib`)

- Detection: `chi.NewRouter()` OR `net/http` patterns in go.mod context
- ORM bindings: sqlc (codegen), GORM
- Doc format: Go doc comments (`// Function does X`)
- Test framework: standard `testing` package + testify
- Few-shot: 8 examples covering router setup, handler funcs, middleware chains, context propagation, struct DTOs, sqlc-generated queries, table-driven tests, error handling idioms
- Known idioms: error wrapping with `%w`, context-first parameter, table-driven tests, panic-recover middleware

### Pack 6+ — community-extensible

The pack format IS the plugin format. Customers can ship their own packs:

- `frameworks/acme-internal-spring/` — Acme's company-specific Spring conventions
- `frameworks/healthcare-fhir/` — domain-specific (FHIR resources, HL7 patterns)

This is exactly the marketplace shape from ADR-0052 P6. A pack IS a plugin.

---

## File ownership for THIS PR (parallel-safe)

```
frameworks/                                                # NEW DIRECTORY
frameworks/_base/                                          # NEW — shared defaults / fallbacks
frameworks/_base/pack.yaml                                  # the universal-default pack
frameworks/_base/semantic_tags.py                           # generic patterns (DATABASE_URL, REDIS_URL etc.)
frameworks/_base/few_shot_library.py                        # 5 framework-agnostic examples
frameworks/java-spring/                                     # NEW — Pack 1
frameworks/java-spring/{pack.yaml, semantic_tags.py, orm_bindings.py, doc_format.py, test_framework.py, few_shot_library.py, known_idioms.json, README.md, SKILL.md}
frameworks/python-fastapi/                                  # NEW — Pack 2
frameworks/python-fastapi/{...same 8 files...}
frameworks/typescript-nestjs/                               # NEW — Pack 3
frameworks/typescript-nestjs/{...same 8 files...}
frameworks/python-django/                                   # NEW — Pack 4
frameworks/python-django/{...same 8 files...}
frameworks/go-chi/                                          # NEW — Pack 5
frameworks/go-chi/{...same 8 files...}

company-brain-ai/src/companybrain/calibration/             # NEW — the loader + context
company-brain-ai/src/companybrain/calibration/__init__.py
company-brain-ai/src/companybrain/calibration/loader.py
company-brain-ai/src/companybrain/calibration/context.py
company-brain-ai/src/companybrain/calibration/manifest.py
company-brain-ai/src/companybrain/extractors/orm_binding/  # NEW — per-ORM extractor modules
company-brain-ai/src/companybrain/extractors/orm_binding/jooq.py        # extracted from ADR-0058
company-brain-ai/src/companybrain/extractors/orm_binding/spring_jpa.py  # NEW
company-brain-ai/src/companybrain/extractors/orm_binding/sqlalchemy.py  # NEW
company-brain-ai/src/companybrain/extractors/orm_binding/tortoise.py    # NEW
company-brain-ai/src/companybrain/extractors/orm_binding/typeorm.py     # NEW
company-brain-ai/src/companybrain/extractors/orm_binding/prisma.py      # NEW
company-brain-ai/src/companybrain/extractors/orm_binding/drizzle.py     # NEW
company-brain-ai/src/companybrain/extractors/orm_binding/sqlc.py        # NEW
company-brain-ai/src/companybrain/extractors/orm_binding/django_orm.py  # NEW
company-brain-ai/src/companybrain/extractors/doc/                       # NEW
company-brain-ai/src/companybrain/extractors/doc/{javadoc.py, jsdoc.py, python_docstring.py, go_doc.py}
company-brain-ai/src/companybrain/extractors/test/                      # NEW
company-brain-ai/src/companybrain/extractors/test/{junit.py, pytest.py, jest.py, gotest.py}

tests/unit/test_calibration_loader.py                                    # NEW
tests/acceptance/test_pack_python_fastapi.py                             # NEW
tests/acceptance/test_pack_typescript_nestjs.py                          # NEW
tests/acceptance/test_polyglot_repo.py                                   # NEW
fixtures/                                                                  # NEW — minimal fixtures per pack
fixtures/sample-spring/                                                    (already implicit via network-iq)
fixtures/sample-fastapi/                                                   # NEW
fixtures/sample-nestjs/                                                    # NEW
fixtures/sample-django/                                                    # NEW
fixtures/sample-go-chi/                                                    # NEW
```

Append-only edits to:

```
company-brain-ai/src/companybrain/extractors/config_extractor.py    # delegate to ctx.semantic_tags
company-brain-ai/src/companybrain/extractors/dispatch.py            # consult ctx.orm_extractors
company-brain-ai/src/companybrain/extractors/javadoc_extractor.py   # rename → doc_extractor.py; delegate to ctx.doc_parser
company-brain-ai/src/companybrain/extractors/test_spec_extractor.py # delegate to ctx.test_parser
company-brain-ai/src/companybrain/pipeline/business_context_v2_prompt.py  # delegate to ctx.few_shot_examples
company-brain-ai/src/companybrain/pipeline/idiom_detector.py        # seed from ctx.known_idioms
company-brain-ai/src/companybrain/harness/skills.py                  # extend detect_framework → load_packs
company-brain-ai/src/companybrain/pipeline/orchestrator.py           # build CalibrationContext at job start; pass to all stages
company-brain-ai/src/companybrain/config.py                          # frameworks_dir setting (default: ./frameworks)
```

---

## Polyglot repo handling

A monorepo with Spring backend + Next.js frontend should load BOTH packs:

```python
matched = detect_all_matching_packs(repo_path)
# returns: [java-spring (priority 100, scope=apps/service/), nextjs (priority 90, scope=apps/web/)]
```

Per-file dispatch: when extracting a file under `apps/service/`, the java-spring pack supplies calibration. Under `apps/web/`, the nextjs pack does. Files outside both scopes fall back to `_base/`.

The `pack.yaml` gets an optional `scope_glob` field for this:

```yaml
id: java-spring
detection:
  any_of: [...]
scope_glob: "apps/service/**"     # optional; defaults to "**" (whole repo)
```

---

## Acceptance test

```python
async def test_python_fastapi_repo_extracts_correctly():
    """Run the brain on a sample FastAPI repo. Assert:
    - Detected pack = python-fastapi
    - SQLAlchemy bindings extracted
    - Pydantic models recognised as DTOs
    - pytest test methods → BehavioralSpec entities
    - BC v2 examples used were FastAPI-specific
    """
    result = await run_pipeline_harness(repo="fixtures/sample-fastapi")
    assert result.telemetry["packs_loaded"] == ["python-fastapi"]
    bc = await brain_get_context("get_user")    # a route in the fixture
    assert "Depends" in bc.engineering_notes[0]   # Pack-specific terminology


async def test_typescript_nestjs_repo_extracts_correctly():
    result = await run_pipeline_harness(repo="fixtures/sample-nestjs")
    assert "typescript-nestjs" in result.telemetry["packs_loaded"]
    # ORM binding extractor for TypeORM should have produced entities
    typeorm_entities = await brain_query("EntityClass with annotation @Entity")
    assert len(typeorm_entities) > 0


async def test_polyglot_monorepo_loads_two_packs():
    result = await run_pipeline_harness(repo="fixtures/sample-polyglot")
    assert set(result.telemetry["packs_loaded"]) == {"java-spring", "nextjs"}
    # File from apps/service/ uses java-spring calibration
    java_bc = await brain_get_context("apps/service/.../UserController")
    assert any("Spring" in n for n in java_bc.engineering_notes)
    # File from apps/web/ uses nextjs calibration
    next_bc = await brain_get_context("apps/web/pages/api/users")
    assert any("Next.js" in n for n in next_bc.engineering_notes)


async def test_no_pack_loaded_falls_back_to_base():
    """If no framework matches, _base pack provides minimal calibration."""
    result = await run_pipeline_harness(repo="fixtures/sample-bash-scripts")
    assert result.telemetry["packs_loaded"] == ["_base"]
    # Brain still extracts SOMETHING — files, configs — using base regex tags
    assert result.telemetry["entity_count"] > 0


async def test_customer_pack_overrides_bundled():
    """A customer can drop a frameworks/acme-spring/ pack that wins via priority."""
    # Setup: install fixtures/plugins/acme-spring as a calibration pack
    result = await run_pipeline_harness(repo="fixtures/sample-spring")
    assert "acme-spring" in result.telemetry["packs_loaded"]
    # Acme pack's known_idioms.json adds a custom pattern; assert it's detected
    patterns = await brain_query("Pattern entities mentioning 'acme'")
    assert len(patterns) > 0
```

---

## Effort estimate

8 days total, parallelisable:

| Workstream | Effort | Sequencing |
|---|---|---|
| Calibration loader + CalibrationContext | 1 day | First; unblocks all packs |
| ORM binding extractors (8 modules) | 2 days | Parallel after loader |
| Doc parsers (4 modules) | 1 day | Parallel after loader |
| Test parsers (4 modules) | 1 day | Parallel after loader |
| Pack: java-spring | 0.5 day | Refactor existing into pack format |
| Pack: python-fastapi | 1 day | New |
| Pack: typescript-nestjs | 1 day | New |
| Pack: python-django | 0.5 day | New |
| Pack: go-chi | 0.5 day | New |
| Polyglot dispatch + acceptance | 1.5 days | Last |

With 3-4 parallel Claude Code sessions: **~3 calendar days** to ship all 5 packs + the loader.

---

## Trade-off note

A "purity" alternative: make the agnostic shells work without ANY pack ("just use the LLM to figure out everything"). Rejected because:

1. The LLM is good at extraction but NOT at inferring "this regex pattern in my config file means database_url" — that requires repository-specific or framework-specific knowledge that's deterministic, not probabilistic.
2. ORM bindings (jOOQ Tables.java, Prisma client.ts, sqlc-generated _gen.go) are formally structured — parsing them with the LLM is wasteful when a Python parser does it for free.
3. Few-shot examples ARE the calibration. Generic examples produce generic answers.

The pack format provides the right abstraction: deterministic where determinism is cheap, LLM where the LLM adds value, customer-extensible where conventions are local.

---

## Action items

1. [ ] `calibration/manifest.py` — pack.yaml parser + validation.
2. [ ] `calibration/loader.py` — pack discovery, detection rule evaluation, priority sort.
3. [ ] `calibration/context.py` — composes multiple matched packs into one runtime context.
4. [ ] `extractors/orm_binding/` — refactor existing jOOQ + add 8 new ORM modules.
5. [ ] `extractors/doc/` — 4 docstring parsers.
6. [ ] `extractors/test/` — 4 test-framework parsers.
7. [ ] Refactor ADR-0057 shells to delegate to context (5 file edits).
8. [ ] Refactor ADR-0058 shells to delegate (3 file edits).
9. [ ] Refactor ADR-0060 shells to delegate (2 file edits).
10. [ ] Build 5 pack directories with their 8 files each.
11. [ ] 5 sample fixture repos (1 per ecosystem).
12. [ ] Polyglot dispatch + acceptance suite.
13. [ ] Update `harness/skills.py::detect_framework` to return list, not single value.

---

## Companion implementation prompt

`SONNET-IMPLEMENTATION-PROMPT-ADR-0062.md` (to write next if you confirm). Will sequence: (a) loader + context first; (b) per-pack workstreams in parallel; (c) shell refactors after loader is stable; (d) acceptance gates per pack.
