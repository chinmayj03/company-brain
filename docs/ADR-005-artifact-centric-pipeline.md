# ADR-005: Artifact-Centric Knowledge Pipeline

**Status:** Proposed
**Date:** 2026-04-28
**Deciders:** Chinmay
**Depends on:** ADR-001 (Postgres graph storage), ADR-003 (multi-tenancy via RLS)
**Unblocks:** ADR-004 (universal knowledge schema), proposed merkle/tree-sitter work, multi-domain collectors, Skills File API

---

## Context

### What we have today

The pipeline ingests one kind of thing well — **source files attached to a git history** — and routes everything else through that lens. Tickets surface as PR-linked text. User annotations surface as commit-anchored strings. The four-pass LLM extraction (`PIPELINE-api-context-builder.md`) is shaped end-to-end around a code artifact and its commit cluster.

This worked for the engineering-domain MVP. It does not work for what comes next.

### What we want next

The stated goal is to evolve from **code context understanding** to **business context understanding**. That means treating these as first-class inputs, not as auxiliary metadata hanging off code:

- A Zendesk ticket describing a refund escalation
- A Confluence page documenting an approval policy
- A Slack thread where an SRE explains why an alert was muted
- A Jira ticket whose description defines a billing rule
- A user annotation written directly in the VS Code extension

Today, none of these can enter the system without being shoehorned into the file-centric pipeline. There is no shared primitive that says *"this is a hashable, sourced, dated, ownable unit of knowledge."*

### Why this is the binding constraint

Both proposed ADRs (merkle/tree-sitter, tiered memory) make the **code-context** layer better. Neither helps when the question is *"the refund policy says 30 days but the code enforces 14 — when did that drift?"* That question requires:

1. A representation of "the refund policy" as something the system has ingested
2. A representation of "the code enforces 14" as something the system has extracted
3. An edge between them
4. A way to detect that the policy changed and re-evaluate the edge

Items 2 and 3 already work. Items 1 and 4 do not, and no current proposal addresses them in a way that scales beyond ad-hoc collectors.

The missing primitive is an **Artifact**: a content-addressed, kind-tagged, source-linked unit that flows through one pipeline regardless of whether it came from a Java file, a Jira ticket, a Slack message, or a hand-typed annotation.

---

## Decision

Introduce **Knowledge Artifacts** as the universal ingestion primitive. Every input — source file, PR description, ticket, annotation, Slack thread, Confluence page, OpenAPI spec — enters the pipeline as an artifact with the same shape:

```
Artifact {
    artifact_id     -- stable, source-derived identifier
    kind            -- 'source_file' | 'ticket' | 'pr' | 'annotation' | 'chat_thread' | 'doc_page' | 'spec'
    content_hash    -- sha256 over normalized content
    workspace_id    -- tenant scope
    source_uri      -- canonical link back to the origin
    raw_content     -- the bytes (or pointer to S3 for large blobs)
    fetched_at      -- when we last pulled it
    author          -- when known
    ...kind-specific metadata...
}
```

Three things follow from this primitive, and only this ADR establishes them:

1. **Collectors produce Artifacts. Period.** A collector's only output contract is a stream of Artifacts. It does not call the LLM, it does not write to the graph, it does not know about nodes or edges. This decouples ingestion (many sources, many languages, many APIs) from extraction (one consistent LLM pipeline).
2. **Merkle invalidation works across every kind.** The dirty-set algorithm in proposed ADR-007 stops being "files whose hash changed" and becomes "artifacts whose hash changed, plus nodes that cite them." A ticket update invalidates the business-context synthesis of every node that referenced that ticket — automatically, by mechanism, not by collector-specific code.
3. **Provenance becomes a graph property, not a string field.** Every node carries explicit edges back to the artifacts it was derived from. When AI Ask cites a source, it cites a real, fetchable, hash-verified artifact — not a paraphrase the LLM emitted.

This ADR establishes the artifact layer. ADRs 006+ (collector framework, tree-sitter symbol index, tiered memory, skills API) build on top.

---

## Options Considered

### Option A: Keep collectors source-specific (current state)

Each new domain (support, finance, ops) gets its own ingestion code path that walks straight into the LLM extractor.

| Dimension | Assessment |
|---|---|
| Time to first ticket source ingested | Fast (just write Zendesk → LLM glue) |
| Maintenance over 5 collectors | Bad — five different "what changed?" stories |
| Merkle invalidation | Per-collector; impossible to make consistent |
| Provenance | String fields in node_context; not verifiable |
| Multi-collector dedup | Manual per pair |

**Verdict:** Acceptable for the first one or two collectors. Becomes the binding constraint by collector three. No invalidation story unifies.

### Option B: Generic event bus, no shared schema

Push everything onto a queue (extending ADR-002's SQS pattern), let consumers shape it however they want.

| Dimension | Assessment |
|---|---|
| Decoupling | Good |
| Replay / re-extraction | Hard — events are not content-addressed |
| Schema evolution | Each consumer reinvents shape |
| Provenance verifiability | None — no canonical content store |

**Verdict:** Solves transport, not representation. The hard problem isn't moving bytes — it's having a stable identity for *"this exact version of this ticket"* that we can hash, cite, and invalidate against.

### Option C: Artifact-Centric Pipeline (recommended)

Every collector emits Artifacts. Artifacts are hashed, stored, and referenced by ID throughout the rest of the pipeline. The LLM extractor consumes Artifacts, not source-specific shapes.

| Dimension | Assessment |
|---|---|
| Time to first ticket source | Slightly slower (must define the kind) |
| Maintenance over 5 collectors | Linear; each collector is a thin Artifact emitter |
| Merkle invalidation | Uniform: hash diff over `artifacts` table |
| Provenance | First-class: edges from node → artifact |
| Multi-collector dedup | One algorithm: `UNIQUE(workspace_id, kind, artifact_id)` |
| Replay | Trivial — re-run extractor over any artifact set |
| Multi-domain | Built in by construction |

**Verdict:** Higher upfront effort (new tables, new collector contract, refactor of the four-pass extractor's input shape). All other improvements compound on this foundation. The merkle layer of ADR-007, the universal schema of ADR-004, the Skills File API of `ARCHITECTURE-v2.md`, and the staleness engine all need this primitive to work cleanly.

---

## Architecture

### Schema

Add three tables. No breaking changes to existing `nodes`, `edges`, `node_context`.

```sql
-- Every ingested unit, regardless of source
CREATE TABLE artifacts (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    workspace_id    UUID NOT NULL,
    kind            TEXT NOT NULL,    -- enum enforced at app layer for flexibility
    external_id     TEXT NOT NULL,    -- e.g. file path, ticket id, commit sha
    content_hash    TEXT NOT NULL,    -- sha256 over normalized content
    source_uri      TEXT,             -- canonical link back to origin
    content_ref     TEXT,             -- inline OR s3://... pointer for large blobs
    content_inline  BYTEA,            -- encrypted at rest if sensitive
    author          TEXT,
    fetched_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_hash  TEXT,             -- previous hash; null on first sight
    metadata        JSONB DEFAULT '{}'::jsonb,
    UNIQUE (workspace_id, kind, external_id)
);

-- Provenance: which nodes were derived from which artifacts
CREATE TABLE artifact_links (
    artifact_id     UUID NOT NULL REFERENCES artifacts(id) ON DELETE CASCADE,
    workspace_id    UUID NOT NULL,
    node_id         UUID NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
    link_role       TEXT NOT NULL,   -- 'derived_from' | 'cited_in_context' | 'invalidates'
    confidence      NUMERIC(3,2) DEFAULT 1.0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (artifact_id, node_id, link_role)
);

-- Append-only invalidation log; the dirty-set engine consumes this
CREATE TABLE artifact_change_events (
    id              BIGSERIAL PRIMARY KEY,
    workspace_id    UUID NOT NULL,
    artifact_id     UUID NOT NULL,
    event_kind      TEXT NOT NULL,   -- 'created' | 'changed' | 'deleted'
    old_hash        TEXT,
    new_hash        TEXT,
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    consumed_at     TIMESTAMPTZ      -- null until the extractor processes it
);

CREATE INDEX idx_artifacts_workspace_kind ON artifacts(workspace_id, kind);
CREATE INDEX idx_artifact_links_node       ON artifact_links(workspace_id, node_id);
CREATE INDEX idx_artifact_links_artifact   ON artifact_links(workspace_id, artifact_id);
CREATE INDEX idx_change_events_unconsumed  ON artifact_change_events(workspace_id, consumed_at)
    WHERE consumed_at IS NULL;

-- RLS, consistent with ADR-003
ALTER TABLE artifacts             ENABLE ROW LEVEL SECURITY;
ALTER TABLE artifact_links        ENABLE ROW LEVEL SECURITY;
ALTER TABLE artifact_change_events ENABLE ROW LEVEL SECURITY;
-- (policies elided; same pattern as nodes/edges)
```

### Collector Contract

A collector is anything that produces Artifacts. It has exactly one method:

```python
class Collector(Protocol):
    kind: str  # 'git', 'zendesk', 'slack', 'confluence', 'annotation', ...

    def collect(
        self,
        workspace_id: UUID,
        since: datetime | None = None,
    ) -> Iterator[Artifact]: ...
```

Collectors do not call the LLM. They do not write to `nodes` or `edges`. They emit Artifacts and exit. Insertion is centralized in `ArtifactWriter`, which:

1. Computes `content_hash`
2. Looks up existing artifact by `(workspace_id, kind, external_id)`
3. If new → insert + emit `created` change event
4. If hash unchanged → no-op (artifact is clean)
5. If hash changed → update + emit `changed` change event with `old_hash`/`new_hash`

This is the only place dirty-set events are produced. Every downstream consumer reads from `artifact_change_events`.

### Dirty-Set Computation (replaces file-merkle in ADR-007)

```python
def compute_dirty_nodes(workspace_id: UUID) -> set[UUID]:
    """Find every node whose synthesis is invalidated by a recent artifact change."""

    changed_artifacts = sql("""
        SELECT artifact_id FROM artifact_change_events
        WHERE workspace_id = %s AND consumed_at IS NULL
    """, workspace_id)

    # Direct: nodes derived from a changed artifact
    direct_dirty = sql("""
        SELECT DISTINCT node_id FROM artifact_links
        WHERE workspace_id = %s
          AND artifact_id = ANY(%s)
          AND link_role IN ('derived_from', 'cited_in_context')
    """, workspace_id, list(changed_artifacts))

    # Transitive: nodes whose call chain reaches a directly-dirty node
    # (graph traversal over edges; bounded depth)
    transitive_dirty = traverse_reverse(direct_dirty, edge_types=['CALLS', 'READS_TABLE'], depth=2)

    return direct_dirty | transitive_dirty
```

Two things matter here. First, this is **kind-agnostic**: a changed Jira ticket invalidates nodes through the same code path as a changed Java file. Second, it cleanly extends to the structural propagation that ADR-007 described — the file-import edges become one of several edge types feeding the transitive closure.

### Pipeline Wiring

```
┌──────────────────────────────────────────────────────────────────┐
│  Collectors (git, zendesk, slack, annotation, openapi, ...)      │
│  Each emits an Iterator[Artifact]                                │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│  ArtifactWriter                                                  │
│  • hash + dedup + change event                                   │
│  • encryption for sensitive kinds (per ADR-003)                  │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│  Dirty-Set Engine                                                │
│  • reads artifact_change_events                                  │
│  • computes dirty_nodes via artifact_links + reverse traversal   │
│  • for first-ingest of an artifact: dirty = "needs extraction"   │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│  Four-Pass LLM Extractor (existing, with input shape change)     │
│  • Input: bundle of Artifacts to (re)process                     │
│  • Output: nodes, edges, node_context, AND artifact_links        │
│           (every node MUST link back to the artifacts that       │
│            produced it — provenance is mandatory)                │
└────────────────────────────────┬─────────────────────────────────┘
                                 │
                                 ▼
┌──────────────────────────────────────────────────────────────────┐
│  Mark events consumed; downstream retrieval (ADR-008+) sees      │
│  fresh nodes with verifiable provenance edges                    │
└──────────────────────────────────────────────────────────────────┘
```

### Provenance enforcement

The four-pass extractor today emits a `source_quote` string field per entity. After this ADR, that field is **replaced by an `artifact_links` row**. The contract: every node row MUST have at least one `artifact_links` row with `link_role = 'derived_from'`. A node without provenance fails write validation.

This is the single most important downstream effect. It makes "show me the source" a real graph traversal — not a string lookup that might or might not still resolve. It makes hallucinated nodes structurally impossible to insert.

---

## Trade-off Analysis

### Why a separate `artifacts` table instead of folding into `node_context`

`node_context` is **derived knowledge** (LLM synthesis, extracted business context, summaries). `artifacts` are **raw inputs** (the unmodified source we extracted from). Conflating them loses two things:

1. The ability to re-extract: if synthesis improves, you re-run the LLM over the same artifacts and overwrite the contexts. If the artifact is the context, you've lost the source.
2. The ability to verify: provenance citations point back to bytes, not paraphrases.

### Why content-hash, not version number or timestamp

Versions assume the source system gives them. Slack threads, file edits between commits, and webhook-delivered events do not. Timestamps drift across systems. A SHA-256 over the normalized content is the only identifier that's source-independent and tamper-evident.

### Why an append-only event log instead of a flag column

A `dirty BOOLEAN` column on `artifacts` would be simpler. But:

- Multiple downstream consumers may want different consumption checkpoints (extraction, embedding refresh, summary regeneration).
- Replay and audit need the historical log.
- The pattern matches `edge_events` (already in the schema), so operational tooling generalizes.

### Why this isn't blocked on ADR-004 (universal knowledge schema)

ADR-004 changes the **node** schema. This ADR changes the **input** layer. They are orthogonal axes. You can land artifact-centric ingestion against today's `nodes` table; if ADR-004 lands later, only `artifact_links.node_id` needs to point at `knowledge_nodes` instead. The migration is a foreign-key retarget, not a rewrite.

---

## Consequences

### What becomes easier

- Adding a new domain is *one collector class*. No pipeline forking.
- "Re-extract everything that touches refund policy" is one SQL statement: find the policy artifact, walk `artifact_links`, mark dirty.
- Provenance answers ("why did the system say this?") become deterministic — every node has fetchable, hash-verified sources.
- Merkle/tree-sitter work (ADR-007) becomes a specialization: source files are one `kind`, with extra structural extraction. The invalidation framework is shared.
- Tiered memory (proposed ADR-008) gets a clean fact: the T0/T1/T2 representations are computed *from* the artifacts a node links to. Tier regeneration triggers off `artifact_change_events`.
- Skills File API gets a concrete answer to *"what is this skill grounded in?"* — the artifacts linked to its constituent nodes.

### What becomes harder

- Collectors must conform to a new contract. Existing git collector needs refactoring (1–2 days of work).
- The four-pass extractor's input shape changes from "code + commit cluster" to "Artifact bundle." This is the largest single change — the four prompts need to be parameterized by `artifact.kind` so they prompt differently for a ticket vs. a source file.
- Storage cost: artifacts are stored in addition to derived nodes. Mitigation: large blobs go to S3 via `content_ref`; small ones live inline.

### What we will need to revisit

- **Encryption granularity.** Today `node_context.body` is encrypted. After this ADR, the *artifacts* are the sensitive raw input. Encryption strategy needs to apply at the artifact layer, possibly per-kind (Slack threads more sensitive than OpenAPI specs).
- **Retention policy.** Some artifacts (deleted Slack messages, redacted tickets) need explicit deletion paths. The append-only events table needs a tombstoning convention.
- **Dedup across collectors.** A PR description ingested both via the git collector and the GitHub API collector should resolve to one artifact. Resolution rule: `(kind, external_id)` is the dedup key; collectors agree on `external_id` formation per kind (this becomes a small spec doc per kind).

---

## Action Items

1. [ ] Write Flyway migration `V2__create_artifact_tables.sql` for the three new tables + RLS policies + indexes.
2. [ ] Implement `ArtifactWriter` service in the Java backend: hash, upsert, emit change event.
3. [ ] Define `Collector` protocol in the Python AI service; refactor existing git collector to emit Artifacts (kind=`source_file`, kind=`pr`, kind=`commit`).
4. [ ] Implement `DirtySetEngine`: reads `artifact_change_events`, computes dirty node set via `artifact_links` + bounded reverse traversal.
5. [ ] Update the four-pass extractor: input is `list[Artifact]`, output mandatorily includes `artifact_links` rows. Reject node writes without provenance.
6. [ ] Backfill existing nodes with `artifact_links` where derivable from existing `node_context.source_id`. Nodes that cannot be backfilled get a synthetic `kind=legacy` artifact so the invariant holds.
7. [ ] Implement two new collectors as the validation cases: `annotation` (already exists as feature, becomes a collector) and one external collector — recommend `zendesk_ticket` for the business-context proof point.
8. [ ] Wire dirty-set into `Orchestrator.run_pipeline()`: only changed artifacts and their dependents get sent to the LLM.
9. [ ] Surface `artifact_links` in the AI Ask citation path: every cited claim links to `source_uri` of the underlying artifact.
10. [ ] Document `(kind, external_id)` formation rules per collector kind in `docs/COLLECTORS.md`.

---

## Why this is the right next ADR

Of the candidates on the table, this is the one that:

- **Is foundational.** Tree-sitter, tiered memory, universal schema, staleness engine, Skills File API all assume an answer to "what's the unit of knowledge that flows through this system?" Today the answer is "a code file in git." This ADR makes the answer "any Artifact."
- **Directly advances business context.** The first time you ingest a Zendesk ticket through this pipeline and AI Ask cites it as a source for a refund-related node, the company brain has crossed from code-only to multi-domain. That's the demo.
- **Is implementable now.** No dependency on ADR-004's schema migration. Self-contained. Roughly two weeks of focused work for one engineer.
- **Reduces, not increases, surface area.** Future collectors are smaller, not larger. The four-pass extractor gets one input shape, not five.
- **Pays back invalidation cost.** Every other proposal eventually needs "what changed and what's affected?" This ADR builds it once, in a kind-agnostic way, instead of three times for three different change sources.

If only one ADR ships next, this is the one.
