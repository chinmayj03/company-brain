# Implementation Prompt — ADR-0052 Phase 6 (marketplace + scheduled + notebook + image + verifier + notes)

**Single-PR Claude Code session. ~5 days. Adds the ecosystem layer: plugin marketplace, scheduled tasks, .ipynb support, vision-based diagram extraction, browser-verifier sub-agent, per-entity sticky notes, artifact pinning.**

---

## Pre-flight

1. Read ADR-0052 §"Phase 6".
2. Verify P5 is on `main`:
   ```bash
   git log --oneline main | head -100 | grep -q "ADR-0052 P5" || exit 1
   ```
3. `git checkout -b feature/adr-0052-p6-marketplace-and-ecosystem`.

---

## File ownership for THIS PR

CREATE / MODIFY exclusively:

```
src/companybrain/harness/plugins.py
src/companybrain/harness/scheduler.py
src/companybrain/harness/notebook_chunker.py
src/companybrain/harness/image_extractor.py
src/companybrain/harness/notes.py
src/companybrain/harness/subagents/__init__.py
src/companybrain/harness/subagents/browser_verifier.py
db/migrations/V12__scheduled_tasks.sql
db/migrations/V13__entity_notes.sql
db/migrations/V14__entity_pinning.sql
docs/PLUGIN-AUTHORING.md
tests/unit/test_plugins.py
tests/unit/test_scheduler.py
tests/unit/test_notebook_chunker.py
tests/unit/test_image_extractor.py
tests/unit/test_notes.py
tests/acceptance/test_harness_p6_marketplace.py
fixtures/plugins/acme-spring-boot/                  # test fixture
fixtures/plugins/acme-spring-boot/plugin.json
fixtures/plugins/acme-spring-boot/skills/SKILL.md
```

APPEND-ONLY to:

```
src/companybrain/harness/skills.py             # plugins can override bundled skills
src/companybrain/cli.py                        # add `brain plugin install/list`, `brain schedule`, `brain note`
src/companybrain/pipeline/code_chunker.py      # delegate .ipynb to notebook_chunker
src/companybrain/api/routes/query.py           # surface entity notes in query response
src/companybrain/models/entities.py            # add pinned + proposed flags
pyproject.toml                                  # apscheduler, nbformat, playwright (browser_verifier)
```

---

## Implementation steps

### 1. Plugin marketplace

`harness/plugins.py`:

```python
"""Plugin install/list/uninstall.

Bundle format: zip containing
  plugin.json     - manifest (name, version, capabilities required)
  skills/         - optional framework skills
  hooks/          - optional hooks
  commands/       - optional slash commands
  tools/          - optional tool definitions
"""
import zipfile
import json
import shutil
from pathlib import Path
from urllib.request import urlretrieve

PLUGIN_HOME = Path.home() / ".brain" / "plugins"


class Plugin:
    def __init__(self, manifest: dict, root: Path):
        self.name = manifest["name"]
        self.version = manifest["version"]
        self.capabilities = manifest.get("required_capabilities", [])
        self.root = root


def install(source: str) -> Plugin:
    """source: local path to .zip OR URL OR plugin name (resolved against registry)."""
    PLUGIN_HOME.mkdir(parents=True, exist_ok=True)
    if source.startswith(("http://", "https://")):
        local_zip = PLUGIN_HOME / Path(source).name
        urlretrieve(source, local_zip)
    else:
        local_zip = Path(source)

    with zipfile.ZipFile(local_zip) as zf:
        manifest = json.loads(zf.read("plugin.json"))
        name = manifest["name"]
        target = PLUGIN_HOME / name
        if target.exists():
            shutil.rmtree(target)
        zf.extractall(target)

    return Plugin(manifest, target)


def list_installed() -> list[Plugin]:
    if not PLUGIN_HOME.exists(): return []
    out = []
    for d in PLUGIN_HOME.iterdir():
        m = d / "plugin.json"
        if m.exists():
            out.append(Plugin(json.loads(m.read_text()), d))
    return out


def discover_skills() -> dict[str, Path]:
    """Plugins extend the framework skill catalogue. Plugin skills take
    precedence over bundled when names collide."""
    out: dict[str, Path] = {}
    for plugin in list_installed():
        skills_dir = plugin.root / "skills"
        if skills_dir.exists():
            for skill_md in skills_dir.glob("**/SKILL.md"):
                fw_name = skill_md.parent.name
                out[fw_name] = skill_md
    return out
```

In `harness/skills.py` (append-only): when loading a skill, check `plugins.discover_skills()` first.

### 2. Scheduled tasks

`harness/scheduler.py` using APScheduler with Postgres job store:

```python
"""Cron-like scheduled extractions. Persisted in scheduled_tasks table."""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.triggers.cron import CronTrigger

_scheduler = None


def get_scheduler() -> AsyncIOScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = AsyncIOScheduler(jobstores={
            "default": SQLAlchemyJobStore(url=os.environ["DATABASE_URL"], tablename="scheduled_tasks")
        })
        _scheduler.start()
    return _scheduler


async def schedule(*, name: str, repo: str, endpoint: str, method: str, cron: str):
    sched = get_scheduler()
    job = sched.add_job(
        _run_extraction,
        trigger=CronTrigger.from_crontab(cron),
        kwargs={"repo": repo, "endpoint": endpoint, "method": method},
        id=name, replace_existing=True,
    )
    return job.id


async def _run_extraction(*, repo, endpoint, method):
    from companybrain.harness.loop import HarnessLoop
    await HarnessLoop().run(...)
```

Migration `V12__scheduled_tasks.sql`:

```sql
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id TEXT PRIMARY KEY,
    next_run_time DOUBLE PRECISION,
    job_state BYTEA NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_scheduled_tasks_next_run ON scheduled_tasks(next_run_time);
```

CLI: `brain schedule daily-rebuild --repo X --endpoint Y --cron "0 2 * * *"`,
`brain schedule list/cancel/run-now <id>`.

### 3. Notebook chunker

`harness/notebook_chunker.py`:

```python
"""Treat each Jupyter cell as a chunk."""
import nbformat
from pathlib import Path
from companybrain.pipeline.code_chunker import MethodChunk


def chunk_notebook(fp: Path) -> list[MethodChunk]:
    nb = nbformat.read(fp, as_version=4)
    chunks = []
    for i, cell in enumerate(nb.cells):
        if cell.cell_type not in ("code", "markdown"):
            continue
        chunks.append(MethodChunk(
            file_path=str(fp),
            qname=f"{fp.stem}.cell_{i}",
            kind="notebook_cell",
            language="python" if cell.cell_type == "code" else "markdown",
            body=cell.source,
            header_context=f"<cell index={i} type={cell.cell_type}>",
            import_context="",
            body_hash=hash(cell.source),
        ))
    return chunks
```

In `pipeline/code_chunker.py` (append-only): if the file ext is `.ipynb`, delegate.

### 4. Image extractor

`harness/image_extractor.py`:

```python
"""Vision-extract architecture diagrams from docs/*.{png,svg,jpg}."""
from pathlib import Path
from companybrain.providers import get_provider, ChatMessage, TaskRole
from companybrain.models.entities import Artifact


_PROMPT = """Analyse this architecture diagram. Identify each labelled
box/component and any directed edges between them. Return JSON:
{
  "components": [{"name":"...", "kind":"service|database|queue|external"}],
  "edges": [{"from":"...","to":"...","label":"..."}]
}"""


async def extract_diagram(image_path: Path) -> Artifact:
    provider = get_provider()
    image_b64 = base64.b64encode(image_path.read_bytes()).decode()
    raw = await provider.chat_json(messages=[
        ChatMessage(role="system", content=_PROMPT),
        ChatMessage(role="user",   content=[
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image_b64}},
            {"type": "text",  "text": "Extract."},
        ]),
    ], role=TaskRole.BALANCED, max_tokens=2_000)
    return Artifact(kind="diagram", external_id=str(image_path),
                    content=raw, metadata={"source_image": str(image_path)})
```

Hook this into the pipeline as a post-extraction enrichment step.

### 5. Browser-verifier sub-agent

`harness/subagents/browser_verifier.py` using Playwright:

```python
"""When a frontend repo is also in the workspace, headless-Chrome the
running app and verify its network calls match the backend's ApiEndpoint
entities. Surfaces drift."""
from playwright.async_api import async_playwright


async def verify(frontend_url: str, brain_endpoints: list[str]) -> dict:
    drift = []
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page()
        observed = []
        page.on("request", lambda req: observed.append(req.url))
        await page.goto(frontend_url)
        await page.wait_for_load_state("networkidle", timeout=10_000)
        await browser.close()
    for obs in observed:
        if not any(ep in obs for ep in brain_endpoints):
            drift.append({"observed_url": obs, "issue": "no matching brain endpoint"})
    return {"observed": observed, "drift": drift}
```

### 6. Per-entity notes

Migration `V13__entity_notes.sql`:

```sql
CREATE TABLE IF NOT EXISTS entity_notes (
    id BIGSERIAL PRIMARY KEY,
    workspace_id UUID NOT NULL,
    entity_urn TEXT NOT NULL,
    note TEXT NOT NULL,
    author TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_entity_notes_urn ON entity_notes(workspace_id, entity_urn);
```

`harness/notes.py`: add/list/delete CRUD. CLI: `brain note add <urn> "..."`. Surface notes inline in `/query` responses (each cited entity's notes appended to the response).

### 7. Artifact pinning

Migration `V14__entity_pinning.sql`:

```sql
ALTER TABLE nodes
  ADD COLUMN IF NOT EXISTS pinned BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS proposed BOOLEAN NOT NULL DEFAULT FALSE;
```

In write paths: if `pinned=TRUE`, do NOT overwrite. If `proposed=TRUE`, exclude from `/query` responses unless `--include-proposed` flag is set.

CLI: `brain pin <urn>` / `brain unpin <urn>` / `brain propose <urn>`.

---

## Acceptance test

```python
@pytest.mark.asyncio
async def test_plugin_install_overrides_bundled_skill():
    await run_cli(["brain","plugin","install","fixtures/plugins/acme-spring-boot.zip"])
    result = await run_pipeline_harness(repo="fixtures/spring-boot-sample", ...)
    assert result.telemetry["skill_loaded_path"].endswith("acme-spring-boot/skills/SKILL.md")


@pytest.mark.asyncio
async def test_scheduled_task_persists_and_runs():
    task = await run_cli(["brain","schedule","test","--repo","...","--endpoint","...","--cron","* * * * *"])
    await asyncio.sleep(70)
    last_run = await get_last_run(task.id)
    assert last_run.success


@pytest.mark.asyncio
async def test_notebook_extracts_cells():
    result = await run_pipeline_harness(repo="fixtures/ml-notebook-sample", ...)
    assert result.entity_count_by_type.get("NotebookCell", 0) == 3


@pytest.mark.asyncio
async def test_diagram_extracted_as_artifact():
    result = await run_pipeline_harness(repo="fixtures/repo-with-diagram", ...)
    diagrams = [a for a in result.artifacts if a.kind == "diagram"]
    assert len(diagrams) >= 1


@pytest.mark.asyncio
async def test_notes_surface_in_query():
    await run_cli(["brain","note","add","urn:cb:dev:code:network-iq:method:Foo.bar","Deprecated 2026-Q4"])
    response = await run_cli_json(["brain","query","--json","what does Foo.bar do?"])
    assert "Deprecated 2026-Q4" in str(response.get("notes", []))


def test_pinned_entity_not_overwritten(db):
    db.execute("UPDATE nodes SET pinned=true WHERE urn=$1", ENTITY_URN)
    asyncio.run(run_pipeline_harness(...))
    row = db.fetchone("SELECT * FROM nodes WHERE urn=$1", ENTITY_URN)
    assert row["last_modified_commit"] == ORIGINAL_COMMIT   # not overwritten
```

---

## PR description

```
feat(harness): marketplace + scheduled + notebook + image + verifier + notes (ADR-0052 P6)

Adds:
- Plugin marketplace (brain plugin install) with manifest-based bundles
- APScheduler-backed scheduled extractions (brain schedule)
- Notebook (.ipynb) chunker — per-cell entities
- Vision-based diagram extractor for docs/*.{png,svg,jpg}
- Browser-verifier sub-agent (Playwright; frontend↔brain parity)
- Per-entity sticky notes (brain note add/list/delete)
- Artifact pinning (pinned/proposed flags on nodes)

Migrations: V12 scheduled_tasks, V13 entity_notes, V14 entity_pinning.
```
