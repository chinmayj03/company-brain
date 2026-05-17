# ADR-0092 — Multi-Source Connector Framework

**Status:** Accepted  
**Date:** 2026-05-17  
**Author:** Chinmay Jadhav  
**Wave:** V2 B1.2  
**Depends on:** ADR-0091 (domain entity first), ADR-0074 (source registry)  
**Supersedes:** ADR-0074 §4 (BaseConnector definition — elevated from FileChunk to SourceArtifact)

---

## Context

ADR-0074 defined the source registry and sketched a `BaseConnector` interface that yields `FileChunk[]`. ADR-0091 established that domain entities are the primary addressable unit and source artifacts are evidence. B1.2 reconciles these two: the connector abstraction needs to operate at the `SourceArtifact` level (not raw `FileChunk`) so that artifacts carry URNs, TTL classes, and domain-resolution metadata before they reach the brain store.

The problems driving this ADR:

1. The ADR-0074 `BaseConnector.chunks()` API returns `FileChunk` — a low-level, markup-bearing struct with no TTL class and no URN. Downstream code (PII scan, entity resolution) has to re-derive this information.
2. There is no plugin registry. Adding a new connector requires editing the orchestrator. Each new source type becomes a new `if source_kind == "...":` branch.
3. The ingestion pipeline is entangled with the LLM extraction pipeline. Non-code sources (Notion, Slack) do not go through AST extraction — they need a clean path to the brain store.

---

## Decision

### 1. Elevate the connector unit to `SourceArtifact`

Replace `FileChunk` (from ADR-0074) with `SourceArtifact` as the unit exchanged between connectors and the ingestion pipeline:

```python
@dataclass
class SourceArtifact:
    urn: str           # source://<type>/<kind>/<id>@<workspace_id>
    title: str
    content: str       # normalized plain text, ready for embedding
    metadata: dict
    last_modified: datetime
    source_type: str
    ttl_class: str     # ADR-0064: permanent | operational | ephemeral | volatile
```

**Why URN at the artifact level?** ADR-0091 §2 requires that every piece of evidence is addressable. Assigning URNs at the connector layer (not at ingest time) means each connector controls its own identity scheme and the pipeline can deduplicate, invalidate, and re-fetch by URN without re-crawling.

**Why TTL class at the artifact level?** The connector is the only layer that knows the semantic longevity of its content. A Notion architecture page is `permanent`; a Slack DM is `volatile`. Inferring this from content downstream is error-prone.

### 2. Connector interface

```python
class BaseConnector(ABC):
    def __init__(self, config: ConnectorConfig): ...

    @abstractmethod
    async def validate_credentials(self) -> bool: ...

    @abstractmethod
    async def list_artifacts(
        self, since: Optional[datetime] = None
    ) -> AsyncIterator[SourceArtifact]: ...

    @abstractmethod
    async def fetch_artifact(self, artifact_urn: str) -> SourceArtifact: ...

    @abstractmethod
    async def get_sync_cursor(self) -> dict: ...

    def supports_webhooks(self) -> bool: return False

    async def handle_webhook(self, payload: dict) -> list[SourceArtifact]:
        raise NotImplementedError
```

Design choices:

- `list_artifacts(since)` is an async generator, not a coroutine returning a list. This avoids buffering large source datasets in memory and enables the pipeline to process artifacts as they arrive.
- `get_sync_cursor()` returns an opaque dict. The pipeline stores and restores it; connectors choose the internal shape. This decouples the cursor strategy from the pipeline — a git connector might use a commit SHA, a REST API might use a page token.
- `validate_credentials()` is a required method (not optional). Credential validation at instantiation time surfaces auth failures before the sync loop begins.
- Webhook support is opt-in via `supports_webhooks()`. Connectors that do not override it default to `False`, keeping the interface clean for pull-based connectors.

### 3. ConnectorRegistry — plugin pattern

```python
class ConnectorRegistry:
    _registry: dict[str, type[BaseConnector]] = {}

    @classmethod
    def register(cls, source_type: str):
        def decorator(connector_cls): ...
        return decorator

    @classmethod
    def get(cls, source_type: str) -> type[BaseConnector]: ...

    @classmethod
    def list_registered(cls) -> list[str]: ...
```

Self-registration via `@ConnectorRegistry.register("notion")` at module level. Importing a connector module causes it to register. The `connectors/__init__.py` imports all built-in connectors so they are available when the package is imported.

**Why a class-level dict rather than a singleton instance?** The registry must be accessible before any instance is created, and across module boundaries. A class-level dict provides the same semantics without the boilerplate of a module-level singleton or a dependency injection container. Tests use `_reset()` to get isolation.

**Double-registration semantics:**
- Same class registered twice: idempotent (safe for module re-imports).
- Different class under the same type: raises `ValueError`. This makes accidental shadowing visible immediately rather than silently degrading.

### 4. ConnectorIngestionPipeline

```python
class ConnectorIngestionPipeline:
    async def run_sync(
        self,
        source_id: str,
        full: bool = False,
        config: Optional[ConnectorConfig] = None,
    ) -> SyncResult: ...
```

Stage sequence per run:
1. Resolve `ConnectorConfig` (from `config_loader` or passed directly).
2. Look up connector class in `ConnectorRegistry`.
3. Instantiate connector and call `validate_credentials()`.
4. Load stored cursor; compute `since` datetime for incremental sync.
5. Stream artifacts via `list_artifacts(since)`.
6. Per artifact: PII scan → store as `BrainEntity` → count.
7. `commit_run()` on the brain store.
8. Persist new cursor from `get_sync_cursor()`.
9. Return `SyncResult`.

**PII scan is fail-open:** if `companybrain.privacy` is not available (early rollout), the scan is skipped and a debug log is emitted. This prevents the connector framework from being blocked by a missing dependency.

**Store errors are counted, not raised:** if writing one artifact fails, the pipeline continues and counts the failure in `SyncResult.artifacts_failed`. Only credential failures and stream errors abort the sync early.

**Domain entity resolution is stubbed (B1.2):** artifacts are stored as `entity_type="source_artifact"` with `domain_resolved=False`. B2 will add a resolution pass that links source artifacts to canonical `urn:cb:…` entities per ADR-0091.

### 5. CodeConnector — reference implementation

`CodeConnector` wraps `FileWalker` and yields one `SourceArtifact` per extractable source file. It is registered as `"code"` and auto-imported via `connectors/__init__.py`.

B1.2 status: stub. The connector reads file content and produces raw-content artifacts. The existing multi-pass LLM extraction pipeline continues to operate independently. Full integration (CodeConnector driving LLM extraction and returning semantically-enriched artifacts) is planned for B2.

---

## Options Considered

### Option A: Keep ADR-0074 FileChunk interface, add metadata fields
Add `urn`, `ttl_class` to `FileChunk`.

**Rejected because:** `FileChunk` is a code-specific abstraction (`path`, `language`). Non-code sources (Notion pages, Slack messages) do not have meaningful `path` or `language` values. Shoehorning those fields results in a leaky abstraction.

### Option B: Single giant connector class per source type (no registry)
Each source type has a hard-coded import and an `if/elif` dispatch in the orchestrator.

**Rejected because:** every new source type requires editing the orchestrator. The connector framework's value is that adding a source type is adding a file — not editing shared infrastructure.

### Option C: External ingestion microservice with queue
Source connectors run in a separate service and push artifacts to a queue; the pipeline consumes from the queue.

**Deferred to B3+:** adds operational complexity (queue infrastructure, separate deploy). The `BaseConnector` interface already matches a queue-based model — the `list_artifacts` generator can be adapted to publish to a queue without changing the interface. This evolution path is preserved.

### Option D: Chosen — SourceArtifact + ConnectorRegistry + IngestionPipeline
Clean separation: connectors produce `SourceArtifact`, the registry dispatches, the pipeline orchestrates. Each layer is independently testable.

---

## Consequences

### Easier
- Adding a new source type (Notion, Slack, Salesforce) = write one `BaseConnector` subclass, import it in `connectors/__init__.py`, done.
- The pipeline is source-type-agnostic. No orchestrator changes needed for new connectors.
- Unit tests can use a `MockConnector` that yields pre-built `SourceArtifact` objects — no file system or network needed.
- PII scan, TTL enforcement, and domain resolution are applied uniformly to all sources.

### Harder
- `CodeConnector` in B1.2 is a stub — full LLM extraction integration requires B2 work to thread the extraction loop through the connector interface.
- Cursor storage is in-memory by default; production deployments must wire in a `cursor_store` backed by the `workspace_sources` DB table (follow-up: B2 API integration).

### Constraints preserved
- Credential values never logged. `ConnectorConfig.credentials` is an opaque dict; the framework treats it as a black box and no logging call touches it.
- Backward compatibility with the existing `orchestrator.run_pipeline()` is unchanged — `CodeConnector` is additive, not a replacement in B1.2.

---

## File Ownership

```
company-brain-ai/src/companybrain/connectors/
  __init__.py        — public surface, imports built-in connectors
  base.py            — ConnectorConfig, SourceArtifact, BaseConnector
  registry.py        — ConnectorRegistry
  pipeline.py        — ConnectorIngestionPipeline, SyncResult
  code.py            — CodeConnector (stub, Wave B1.2)

company-brain-ai/tests/unit/
  test_connector_base.py
  test_connector_registry.py
  test_connector_pipeline.py
```

---

## Action Items

- [x] Implement `connectors/base.py` — ConnectorConfig, SourceArtifact, BaseConnector
- [x] Implement `connectors/registry.py` — ConnectorRegistry with @register decorator
- [x] Implement `connectors/pipeline.py` — ConnectorIngestionPipeline, SyncResult
- [x] Implement `connectors/code.py` — CodeConnector stub
- [x] Unit tests for all three layers
- [ ] B2: Wire `ConnectorIngestionPipeline` to `workspace_sources` DB (cursor persistence)
- [ ] B2: Full CodeConnector integration with LLM extraction passes
- [ ] B2: Domain entity resolution pass (flip `domain_resolved=True`)
- [ ] B3: NotionConnector, SlackConnector implementations
- [ ] B3+: Webhook ingestion endpoint that calls `handle_webhook()`
- [ ] B3+: Encrypted credential store (replaces env-var-name indirection from ADR-0074)
