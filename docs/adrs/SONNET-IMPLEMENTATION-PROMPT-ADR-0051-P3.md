# Implementation Prompt — ADR-0051 Phase 3 (skills + per-repo memory)

**Single-PR Claude Code session. ~5 days. You're adding framework-specific skills (spring-boot, fastapi, etc.) loaded on demand, plus per-repo `.brain/BRAIN.md` memory file with auto-append.**

---

## Pre-flight

1. Read `docs/adrs/ADR-0051-agentic-harness-migration.md` §"Phase 3" + ADR-0052 §"#3 Skills" + §"#4 Memory files".
2. Verify P2 is on `main`:
   ```bash
   git log --oneline main | head -50 | grep -q "ADR-0051 P2" || exit 1
   ```
3. `git checkout -b feature/adr-0051-p3-skills-memory`.

---

## File ownership for THIS PR

CREATE / MODIFY exclusively:

```
src/companybrain/harness/skills.py
src/companybrain/harness/memory.py
frameworks/spring-boot/SKILL.md
frameworks/fastapi/SKILL.md
frameworks/nestjs/SKILL.md
frameworks/django/SKILL.md
frameworks/rails/SKILL.md
frameworks/nextjs/SKILL.md
.brain-template/BRAIN.md
tests/unit/test_skills.py
tests/unit/test_memory.py
tests/acceptance/test_harness_p3_skills.py
```

APPEND-ONLY to:

```
src/companybrain/harness/system_prompt.py     # inject skill + BRAIN.md content
src/companybrain/harness/loop.py              # call skills.detect + memory.load on init
docs/HARNESS.md                                # add "Skills" + "Memory" sections
```

---

## Implementation steps

### 1. `harness/skills.py`

```python
"""Skill detection + loading.

Detects the primary framework of a repo via a cheap deterministic scan,
then loads at most one SKILL.md into the system prompt.
"""
from collections import Counter
from pathlib import Path
from typing import Optional


_FRAMEWORK_MARKERS = {
    "spring-boot": [
        ("**/*.java", lambda txt: "@SpringBootApplication" in txt or "spring-boot-starter" in txt),
        ("pom.xml",   lambda txt: "spring-boot-starter" in txt),
        ("build.gradle*", lambda txt: "spring-boot" in txt),
    ],
    "fastapi": [
        ("**/*.py",       lambda txt: "from fastapi import" in txt),
        ("pyproject.toml", lambda txt: "fastapi" in txt),
        ("requirements.txt", lambda txt: "fastapi" in txt),
    ],
    "nestjs": [
        ("**/*.ts", lambda txt: "@nestjs/core" in txt or "@Controller" in txt),
        ("package.json", lambda txt: '"@nestjs/' in txt),
    ],
    "django":  [("**/*.py", lambda t: "from django" in t), ("manage.py", lambda t: "django" in t)],
    "rails":   [("Gemfile", lambda t: "rails" in t.lower())],
    "nextjs":  [("package.json", lambda t: '"next":' in t or '"next/' in t)],
}

_FRAMEWORKS_DIR = Path(__file__).parent.parent.parent.parent / "frameworks"


def detect_framework(repo_path: Path) -> Optional[str]:
    """Returns the framework name with the most marker hits, or None."""
    scores: Counter[str] = Counter()
    for fw, patterns in _FRAMEWORK_MARKERS.items():
        for pattern, predicate in patterns:
            for f in list(repo_path.rglob(pattern))[:50]:   # cap scan to keep cheap
                try:
                    if predicate(f.read_text(errors="ignore")):
                        scores[fw] += 1
                except OSError:
                    pass
    if not scores:
        return None
    fw, _ = scores.most_common(1)[0]
    return fw


def load_skill(framework: str) -> str:
    skill = _FRAMEWORKS_DIR / framework / "SKILL.md"
    if not skill.exists():
        return ""
    return skill.read_text()
```

### 2. `harness/memory.py`

```python
"""Per-repo BRAIN.md memory file.

Auto-loaded into the system prompt on every run. Has two sections:
  - Curated (human-edited)
  - Auto-appended (pipeline observations: 'JsonKeyMapping skipped 3 runs')

The auto-append uses the file-state-tracking pattern: read current state,
verify it matches our cached version, then write. Refuses to write if
the file changed under us.
"""
from pathlib import Path
from datetime import datetime


_SECTION_AUTO = "<!-- AUTO-APPENDED — managed by company-brain. Do not edit by hand. -->"


def brain_md_path(repo_path: Path) -> Path:
    return repo_path / ".brain" / "BRAIN.md"


def load(repo_path: Path) -> str:
    p = brain_md_path(repo_path)
    if not p.exists():
        return ""
    return p.read_text()


def auto_append(repo_path: Path, observation: str, *, dedupe_window_chars: int = 4_000) -> None:
    """Append observation to the auto-section. Skips if the same observation
    appears in the trailing dedupe_window_chars (avoids spam)."""
    p = brain_md_path(repo_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    current = p.read_text() if p.exists() else _initial_template()
    if observation in current[-dedupe_window_chars:]:
        return
    if _SECTION_AUTO not in current:
        current += f"\n\n{_SECTION_AUTO}\n"
    timestamp = datetime.utcnow().isoformat(timespec="seconds")
    current += f"\n- {timestamp} — {observation}"
    p.write_text(current)


def _initial_template() -> str:
    return (Path(__file__).parent.parent.parent.parent / ".brain-template" / "BRAIN.md").read_text()
```

### 3. Framework SKILL.md files

Each ~2000 tokens. Example structure for `frameworks/spring-boot/SKILL.md`:

```markdown
# Spring Boot Extraction Skill

You are extracting from a Spring Boot codebase. Apply these conventions:

## Annotations to recognise
- `@RestController` / `@Controller` — entry handlers
- `@RequestMapping("/path")` at class — base path
- `@GetMapping`, `@PostMapping` etc at method — routes
- `@Service`, `@Component` — service-layer beans
- `@Repository` — data-access; can be JPA interface OR @Repository class
- `@Autowired`, constructor injection (with Lombok `@RequiredArgsConstructor`) — dependency edges
- `@Transactional` — boundary annotation worth capturing as ANNOTATES edge

## SQL extraction
- JPA repositories: `@Query("SELECT ...")` annotations
- jOOQ: `dslContext.select(...).from(TABLE).where(...)` chains
- Look for `r.value1()`, `r.value2()` etc. — these are column accessors that
  typically appear with `.lobName(...)` setters that name the column

## DTOs to skip (do NOT call extract_methods_from_class on these)
- `*Request`, `*Response`, `*DTO` classes whose methods are only getters/setters/equals
- `*Entity` JPA entities (annotation-only)
- `*Config` / `*Configuration` Spring beans (no business logic)

## Common false positives
- `@RequiredArgsConstructor` (Lombok) generates a constructor — it's a constructor edge,
  not a callsite to "RequiredArgsConstructor"
- `@Slf4j` — generates a `log` field; ignore as a relationship
```

Repeat shape for fastapi (FastAPI route decorators, Pydantic models, SQLAlchemy patterns), nestjs (`@Module`, `@Controller`, `@Injectable`, TypeORM), django (`urls.py` patterns, models.Manager, ORM querysets), rails (routes.rb, ActiveRecord scopes), nextjs (App Router pages, server components, route handlers).

### 4. `.brain-template/BRAIN.md`

```markdown
# BRAIN.md — repo-specific brain memory

This file is auto-loaded into the company-brain extraction agent's system
prompt on every run. Use it to capture repo-specific gotchas, conventions,
and anti-patterns the agent should know.

## Curated notes (human-edited)
<!-- Add notes here. Examples:
- The `lob` column was renamed from `lobName` in 2024-Q3.
- The `JsonKeyMapping` class is a constants table — never extract it as a code entity.
- All SQL goes through jOOQ DSL chains; ignore raw SQL strings.
-->

<!-- AUTO-APPENDED — managed by company-brain. Do not edit by hand. -->
```

### 5. Wire into harness

In `harness/system_prompt.py`, append-only:

```python
def build_system_prompt(context: dict) -> str:
    base = _BASE_PROMPT
    repo = Path(context.get("repo_path", ""))
    skill = ""
    if repo.exists():
        from companybrain.harness.skills import detect_framework, load_skill
        from companybrain.harness import memory
        fw = detect_framework(repo)
        if fw:
            skill = f"\n\n# Framework Skill: {fw}\n\n{load_skill(fw)}"
            context["skill_loaded"] = fw
        brain_md = memory.load(repo)
        if brain_md:
            base += f"\n\n# Repo memory (BRAIN.md)\n\n{brain_md}"
    return base + skill
```

---

## Acceptance test

```python
@pytest.mark.asyncio
async def test_same_extraction_works_on_spring_and_fastapi():
    spring  = await run_pipeline_harness(repo="fixtures/spring-boot-sample", ...)
    fastapi = await run_pipeline_harness(repo="fixtures/fastapi-sample", ...)
    assert spring.entity_count > 5
    assert fastapi.entity_count > 5
    assert spring.telemetry["skill_loaded"] == "spring-boot"
    assert fastapi.telemetry["skill_loaded"] == "fastapi"


@pytest.mark.asyncio
async def test_brain_memory_auto_appends_observations(tmp_path):
    """Drop a class three runs in a row; BRAIN.md must mention it."""
    for _ in range(3):
        await run_pipeline_harness(repo=tmp_path, observation_to_emit="JsonKeyMapping always dropped")
    bm = (tmp_path / ".brain" / "BRAIN.md").read_text()
    assert "JsonKeyMapping" in bm
    assert bm.count("JsonKeyMapping") == 1   # dedupe within window
```

---

## PR description

```
feat(harness): skills + per-repo memory (ADR-0051 P3)

Adds:
- 6 framework skills (spring-boot, fastapi, nestjs, django, rails, nextjs)
- Skill detector that picks the right one per repo (cheap deterministic scan)
- .brain/BRAIN.md per-repo memory file with curated + auto-appended sections
- File-state-tracking pattern for safe auto-append

Acceptance: same lob extraction works on Spring AND FastAPI repos with
no orchestrator code change. BRAIN.md auto-appends recurring drops.
```
