# B1.2 — ADR-0092 Multi-Source Connector Framework

**Wave:** V2 B1  
**Status:** In Progress  
**ADR:** ADR-0092  
**Depends on:** ADR-0091 (domain entity first), ADR-0074 (source registry)

---

## Problem

ADR-0074 established the source registry and defined `BaseConnector` as a `FileChunk`-yielding
abstraction. B1.2 elevates that to a full connector framework that:

1. Defines `SourceArtifact` (the domain-level unit replacing raw `FileChunk`) tied to the
   ADR-0091 entity-first model.
2. Provides `ConnectorRegistry` — a plugin map from `source_type → connector class`.
3. Provides `ConnectorIngestionPipeline` — orchestrates artifact streaming, PII scan, embedding,
   and brain storage.
4. Ships `CodeConnector` — wraps the existing extraction pipeline as a live proof-of-concept.

---

## Scope

### In scope
- Abstract `BaseConnector` interface with all lifecycle methods
- `ConnectorConfig` and `SourceArtifact` dataclasses
- `ConnectorRegistry` with `@register` decorator pattern
- `ConnectorIngestionPipeline.run_sync()` with `SyncResult`
- `CodeConnector` wrapping `git_local`-style extraction pipeline (stub quality)
- Unit tests for all three layers
- ADR-0092 document

### Out of scope (Wave B2+)
- Notion, Slack, Salesforce connector implementations
- Encrypted credential store (ADR-0074 §3 follow-up)
- Cross-source URN resolution
- Webhook push support beyond the interface stub
