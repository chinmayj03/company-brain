# B1.2 Implementation Plan

## File layout

```
company-brain-ai/src/companybrain/connectors/
  __init__.py          — public surface exports
  base.py              — ConnectorConfig, SourceArtifact, BaseConnector
  registry.py          — ConnectorRegistry
  pipeline.py          — ConnectorIngestionPipeline, SyncResult
  code.py              — CodeConnector (wraps existing pipeline)

company-brain-ai/tests/unit/
  test_connector_base.py
  test_connector_registry.py
  test_connector_pipeline.py

docs/adrs/ADR-0092-multi-source-connector-framework.md
```

## Layers

### Layer 1: Data model (base.py)
- `ConnectorConfig` — source identity + credentials + sync config
- `SourceArtifact` — domain-level artifact with URN, TTL class, content
- `BaseConnector` — ABC with validate / list_artifacts / fetch_artifact / get_sync_cursor
- Optional push interface: `supports_webhooks()` + `handle_webhook()`

### Layer 2: Registry (registry.py)
- Class-level dict `_registry`
- `@ConnectorRegistry.register("type")` decorator
- `get()` raises `KeyError` with friendly message
- `list_registered()` for introspection

### Layer 3: Pipeline (pipeline.py)
- `SyncResult` dataclass (counts, duration, cursor)
- `ConnectorIngestionPipeline.run_sync(source_id, full=False)`
  - Loads config from source registry (in-memory stub for tests)
  - Instantiates connector via registry
  - Streams artifacts
  - PII scan (optional, skips if not available)
  - Stores artifact in brain store
  - Updates cursor
  - Returns SyncResult

### Layer 4: CodeConnector (code.py)
- Wraps `FileWalker` + extractor dispatch
- Proves the interface compiles and type-checks
- Marked as "stub" — full integration in B2
