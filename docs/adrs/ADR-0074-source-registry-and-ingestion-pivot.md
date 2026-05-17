# ADR-0074 — Source Registry & Source-First Ingestion Pivot

**Status:** Proposed  
**Date:** 2026-05-17  
**Author:** Chinmay Jadhav  
**Supersedes:** portions of ADR-0016 (repo-scoped CLI), ADR-0071 (frontend rebuild)  
**Depends on:** ADR-0072 (workspace primitives), ADR-0073 (frontend live-up)

---

## Context

### The problem with the current model

The pipeline's entry point (`AiRunRequest` in `pipeline.py`) accepts `endpoint_path: str` and `http_method: str` — fields that imply the pipeline crawls a live HTTP service endpoint, not a file system. This is a **category error**: the actual job is reading source files, extracting AST, generating business context, and writing to Qdrant + Neo4j.

The consequence is a leaky, confused abstraction:

- Indexing requires the CLI (`python -m companybrain.cli index --repo ...`). There is no UI path to register or index any source.
- `workspace_sources` table exists and `GET /sources` works, but there is no `POST /sources` endpoint — no way to register a source at all except by direct DB writes after a CLI run.
- The Sources view shows an honest "No sources indexed yet — run the pipeline." with no button. Every demo that starts from scratch hits a dead wall.
- Every new source type (OpenAPI specs, Confluence, GitHub PRs, DB migration files) would require a new CLI command. There is no connector abstraction to extend.

### The pivot

**From:** endpoint-centric, CLI-only ingestion where the frontend is a viewer  
**To:** source-registry-first ingestion where the UI is the primary way to connect knowledge sources and the pipeline is source-type-agnostic

The brain becomes a multi-source knowledge platform. A "source" is anything that knows something about your engineering system: a git repo, an OpenAPI spec file or URL, a Confluence space, a DB migration directory, a GitHub repo's PR history. Each source type has a typed connector that normalizes content into `FileChunk[]` before the LLM pipeline ever sees it.

---

## Decision

### 1. Source Registry — schema

Extend `workspace_sources` (already exists) with a `config` JSONB column and make source `kind` an enum:

```sql
-- Migration: add config column + kind enum
ALTER TABLE workspace_sources
  ADD COLUMN IF NOT EXISTS config JSONB NOT NULL DEFAULT '{}';

CREATE TYPE source_kind AS ENUM (
  'git_local',      -- local filesystem clone
  'git_remote',     -- GitHub/GitLab URL (auto-clone)
  'openapi',        -- OpenAPI/Swagger spec (file path or URL)
  'confluence',     -- Confluence space (API token + space key)
  'db_migrations',  -- Flyway/Liquibase migration directory
  'github_prs',     -- GitHub PR + review comment history
  'slack_channel',  -- Slack channel archive (future)
  'notion',         -- Notion workspace (future)
  'jira',           -- Jira project (future)
  'datadog'         -- Datadog SLO/monitor metadata (future)
);

-- Rename kind column to kind enum
ALTER TABLE workspace_sources
  ALTER COLUMN kind TYPE source_kind USING kind::source_kind;
```

Config shape per kind:

```python
# git_local
{"repo_path": "/abs/path/to/repo", "branch": "main", "include_globs": ["**/*.java","**/*.py"]}

# git_remote  
{"clone_url": "https://github.com/acme/payments.git", "branch": "main", "auth_token_env": "GH_TOKEN"}

# openapi
{"spec_path_or_url": "/path/to/openapi.yaml"}  # or "https://..."

# confluence
{"base_url": "https://acme.atlassian.net", "space_key": "ENG", "api_token_env": "CONF_TOKEN"}

# db_migrations
{"migrations_dir": "/path/to/db/migrations", "dialect": "postgresql"}

# github_prs
{"owner": "acme", "repo": "payments", "api_token_env": "GH_TOKEN", "max_prs": 200}
```

### 2. New API endpoints

**POST `/workspaces/{workspace_id}/sources`** — register a source

```python
class RegisterSourceRequest(BaseModel):
    kind: SourceKind
    display_name: str
    config: dict
    auto_index: bool = True  # immediately trigger indexing after registration

class RegisterSourceResponse(BaseModel):
    source: WorkspaceSource
    job_id: Optional[str]  # set if auto_index=True
```

Returns 201. If `auto_index=True`, immediately dispatches an IndexRequest (see §3) and returns `job_id` for polling.

**DELETE `/workspaces/{workspace_id}/sources/{source_id}`** — remove source + its entities

**GET `/workspaces/{workspace_id}/sources/{source_id}/progress`** — live index progress (SSE stream, mirrors existing job polling)

### 3. IndexRequest — source-first pipeline entry point

Replace `AiRunRequest.endpoint_path` + `http_method` with a source-aware request:

```python
class IndexRequest(BaseModel):
    """Source-first pipeline entry point. Replaces endpoint_path + http_method."""
    job_id: str
    workspace_id: str
    source_id: str           # FK into workspace_sources
    source_kind: SourceKind  # redundant but avoids DB lookup in hot path
    config: dict             # source config snapshot (don't re-read DB in pipeline)
    branch: str = "main"
    incremental: bool = False  # True = only re-index changed files since last sync
    callback_url: Optional[str] = None
    callback_key: Optional[str] = None
```

**Migration path — zero breaking changes:**
- `POST /pipeline/run` (Java-initiated) translates to `IndexRequest` internally, preserving the Java integration
- `POST /pipeline/start` (legacy CLI direct-call) auto-registers a `git_local` source row if none exists for that `repo_path`, then dispatches `IndexRequest`
- `companybrain.cli index --repo ...` does the same: upsert source row, dispatch `IndexRequest`

### 4. Typed Connectors

```
company-brain-ai/src/companybrain/connectors/
├── __init__.py
├── base.py              # BaseConnector abstract: yields FileChunk[]
├── git_local.py         # reads local filesystem, respects .gitignore
├── git_remote.py        # clones to temp dir, delegates to git_local
├── openapi_connector.py # parses OpenAPI YAML/JSON → endpoint FileChunks
├── confluence.py        # Confluence REST API → page FileChunks
├── db_migrations.py     # SQL DDL/Flyway → schema FileChunks
└── github_prs.py        # GitHub API → PR description + review FileChunks
```

`BaseConnector` interface:

```python
class FileChunk(TypedDict):
    path: str          # logical path e.g. "GET /competitiveness/plan" or "db/migrations/V1__init.sql"
    content: str       # normalized text content
    language: str      # "java", "sql", "markdown", "openapi-endpoint", etc.
    metadata: dict     # source-specific: {"method": "GET", "tags": [...]} for openapi, etc.

class BaseConnector(ABC):
    @abstractmethod
    async def chunks(self, config: dict, branch: str) -> AsyncIterator[FileChunk]:
        """Yield normalized FileChunks from this source."""
```

The orchestrator becomes connector-agnostic — it calls `connector.chunks()`, feeds results to extractors, done.

### 5. UI — "Add source" flow

The Sources view gets:
1. **Header "Add source" button** — always visible, even when sources exist
2. **Add source modal** — step 1: pick source type (6 cards: Git Repo, OpenAPI, Confluence, DB Migrations, GitHub PRs, Coming soon…); step 2: fill config form for that type; step 3: confirmation + index trigger with live progress
3. **Per-source progress** — during indexing, source card shows a progress bar + current stage label (pulled from SSE stream)
4. **First-run onboarding** — if `sources.length === 0` on first load, show an expanded onboarding card instead of the dead empty state

---

## Options Considered

### Option A: Keep CLI-only, add a "run pipeline" button in UI
Simple. Just adds a button that calls `/pipeline/start` with the existing `repo_path` + `endpoint_path` fields.

**Rejected because:**
- Doesn't solve the category error (`endpoint_path` is still meaningless for file-based repos)
- Doesn't support multiple source types — every new source type needs a new CLI flag
- Doesn't give users any way to manage, re-sync, or remove individual sources

### Option B: Source registry + typed connectors (chosen)
More upfront work but the right abstraction. Every new source type is a new connector file, not a new CLI flag. The UI can surface all source types with type-specific config forms. The pipeline becomes fully source-agnostic.

### Option C: External ingestion service (separate microservice)
Separate service that handles all connectors, feeds normalized chunks to the brain via a queue.

**Rejected because:**
- Over-engineered for current scale
- Adds operational complexity with a second service to run
- Can evolve to this model later when scale demands it — the `BaseConnector` abstraction already matches a queue-based model

---

## Consequences

### Easier
- Adding a new source type = write one `BaseConnector` subclass + add config form in UI
- Frontend can show per-source sync status, last-synced timestamp, entity count, errors
- Re-indexing a single source without touching others
- Incremental indexing (only changed files since last sync)

### Harder
- `workspace_sources` table migration required before deployment
- Existing CLI-triggered indexes need to upsert a source row (handled in migration path §3)
- The `endpoint_path` semantics in `AiRunRequest` need to be deprecated cleanly

### Needs follow-up
- Connector credential management (API tokens) — currently stored as `env_var_name` in config (user sets env var), not as encrypted secrets. Secret management ADR needed for multi-user deployments.
- Incremental indexing strategy — `git_local` can use `git diff HEAD~1` but Confluence/OpenAPI need etag-based diffing
- Source dependency ordering — if a Confluence page links to a class in a git source, cross-source URN resolution needs work

---

## Action Items

- [ ] Write DB migration for `config` column + `source_kind` enum
- [ ] Write `POST /workspaces/{id}/sources` route
- [ ] Write `DELETE /workspaces/{id}/sources/{id}` route  
- [ ] Write `GET /workspaces/{id}/sources/{id}/progress` SSE route
- [ ] Write `BaseConnector` + `GitLocalConnector` (extract from existing orchestrator)
- [ ] Write `OpenAPIConnector`
- [ ] Write `DbMigrationsConnector` (wraps existing `schema_sql.py` extractor)
- [ ] Update orchestrator to accept `IndexRequest` and dispatch to correct connector
- [ ] Update `POST /pipeline/run` to translate to `IndexRequest`
- [ ] Update `POST /pipeline/start` to upsert source row before dispatching
- [ ] Update CLI `index` command to upsert source row
- [ ] Frontend: Add source modal + config forms (see ADR-0075)
- [ ] Frontend: Source card progress bar during indexing
- [ ] Frontend: First-run onboarding card

**Phase 1 (demo-critical):** DB migration + POST /sources + GitLocalConnector + Add source modal  
**Phase 2 (high value):** OpenAPIConnector + DbMigrationsConnector + incremental indexing  
**Phase 3 (growth):** ConfluenceConnector + GitHubPRsConnector + credentials management
