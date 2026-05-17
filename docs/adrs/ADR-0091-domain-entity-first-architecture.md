# ADR-0091 — Domain-Entity-First Architecture

**Status**: Proposed
**Date**: 2026-05-17
**Author**: Company-Brain V2 orchestration
**Builds on**: ADR-0013 (Canonical URN), ADR-0074 (Source Registry), ADR-0085 (V2 Architecture)
**Implemented by**: ADR-0093 (Cross-Source Entity Resolution P1), B1.2 (Connector Framework)
**Supersedes**: nothing; extends ADR-0013 URN model to the domain layer

---

## Context

Company-brain started code-first: entities are Java classes, SQL tables, Python functions, REST endpoints. The URN model (ADR-0013) identifies code artifacts precisely. The extraction pipeline (ADR-0042–ADR-0060) produces a dense, accurate code brain.

As non-code sources arrive — Notion documents, Slack threads, Confluence pages, Salesforce records, call transcripts, email — a new problem emerges: **the same real-world concept exists across multiple sources with different identities**.

The `Customer` entity:
- In Java: `com.company.domain.Customer` (class)
- In Notion: "Customer Lifecycle" (page)
- In Salesforce: `Account` (object) with `Type = Customer`
- In Slack: mentioned as "customer", "client", "account" interchangeably
- In a call transcript: referenced by company name ("Acme") not type name

If each connector ingests its own artifacts independently without cross-source resolution, the brain becomes a **collection of silos**. A query about "Customer" returns code artifacts OR Notion pages OR Salesforce records, never a unified answer. Persona answers cannot span sources. Provenance is opaque. The cross-source value proposition — "we know everything about Customer from every system" — never materialises.

**This ADR makes domain entities the primary addressable unit** and treats source artifacts as evidence about domain entities.

---

## Decision

**Domain entities are the first-class citizens of the brain. Source artifacts are evidence.**

A domain entity:
- Represents a real-world concept that exists in the business (Customer, Order, Feature, Incident, Team)
- Is identified by a domain URN (`domain://customer@workspace-id`)
- Has zero or more source references: code files, doc pages, CRM records, messages
- Has a confidence score per source reference (how certain we are that this artifact is about this domain entity)
- Has cross-source confidence (how certain we are the resolution is correct)

A source artifact (code entity, doc page, Slack thread, Salesforce record) remains a first-class object in its own right, but is always linked to ≥ 0 domain entities via a resolution edge.

Queries at the domain level surface evidence from all linked sources. Queries at the source level surface artifact-specific detail.

---

## Domain Entity Schema Extension

Extend the base entity model with domain-level fields (append-only; existing code entities unaffected if fields are absent):

```python
@dataclass
class DomainEntityRef:
    domain_urn: str                        # domain://customer@workspace-id
    domain_name: str                       # "Customer"
    domain_category: Literal[
        "business_concept",                # Customer, Order, Feature, Team
        "infrastructure",                  # Service, Database, Queue
        "process",                         # Workflow, Pipeline, SLA
        "person",                          # Team member, stakeholder
        "external",                        # Vendor, Partner, Regulation
    ]
    canonical_sources: list[SourceRef]     # all artifacts that evidence this entity
    source_confidence: dict[str, float]    # urn → confidence that artifact = this entity
    cross_source_confidence: float         # overall resolution confidence 0–1
    resolution_method: Literal[
        "name_match",                      # exact name match across sources
        "semantic_embed",                  # embedding similarity > threshold
        "explicit_link",                   # Notion mentions code URN, Slack links PR
        "human_confirmed",                 # user confirmed in BRAIN.md or UI
    ]
    last_resolved_at: datetime
    primary_source: str                    # which source is ground truth for this entity
```

Domain URN format: `domain://<slug>@<workspace-id>` where slug is lowercased, hyphenated domain name (e.g. `domain://customer@acme-corp`).

---

## Cross-Source Entity Resolution Policy

Resolution decides whether artifact A (a Java class) and artifact B (a Notion page) refer to the same domain entity. **ADR-0093 implements this; this ADR defines the policy.**

### Resolution tiers (in priority order)

1. **Explicit link** (highest confidence: 0.95+): The artifact itself contains a link or reference to another artifact. Example: Notion page contains `[Customer Java class](github://...Customer.java)`. Or a Slack message contains `pr#123`. These are unambiguous.

2. **Name match**: Exact match on canonical entity name after normalisation (strip prefixes, CamelCase split, lemmatise). `CustomerService` ↔ "Customer Service" ↔ "customer_service". Confidence 0.75–0.90 depending on name uniqueness.

3. **Semantic embedding similarity**: Embed the entity's description/docstring/surrounding context; embed the doc page abstract; cosine similarity > 0.85. Confidence 0.65–0.80. Required when names diverge (Salesforce uses "Account", code uses "Customer").

4. **Human confirmation** (lowest entropy): User explicitly maps in BRAIN.md or the resolution UI. Confidence 1.0; overrides all automatic methods.

### Resolution thresholds

- `cross_source_confidence ≥ 0.80` → auto-resolve; materialise the domain entity
- `0.60 ≤ cross_source_confidence < 0.80` → suggest to user; don't auto-resolve
- `cross_source_confidence < 0.60` → drop; treat artifacts as unresolved source entities

### Conflict resolution

If two artifacts resolve to the same domain entity via different methods that disagree:
- Explicit link always wins
- Human confirmation wins over automatic methods
- Higher confidence wins between automatic methods

---

## Source Hierarchy

Different sources have different epistemic roles:

| Source type | Role | Trusted for |
|-------------|------|-------------|
| Code | Ground truth for behaviour | "What does it actually do?" |
| Docs (Notion/Confluence) | Intent and requirements | "What should it do? Why?" |
| Messaging (Slack) | Signals and decisions | "What were we thinking? What changed?" |
| CRM (Salesforce) | Customer facts | "Who uses it? What did we promise?" |
| Calls/Email | Contextual decisions | "What was agreed? What's the constraint?" |

When sources conflict:
- Code overrides docs on "what it does"
- Docs override code on "what it should do" (docs may be aspirational)
- Recency wins for factual claims (latest Slack message about a decision > older doc)
- Human confirmations override all automatic sources

---

## URN Design for Domain Entities

Extending ADR-0013 canonical URN to the domain layer:

```
domain://<entity-slug>@<workspace-id>
```

Examples:
- `domain://customer@acme-corp`
- `domain://prior-auth-workflow@network-iq`
- `domain://payments-service@acme-corp`

Resolution edges in the graph:

```
code://com.acme.Customer  --[RESOLVES_TO]--> domain://customer@acme-corp
notion://page/abc123      --[RESOLVES_TO]--> domain://customer@acme-corp  
sf://Account/001abc       --[RESOLVES_TO]--> domain://customer@acme-corp
```

The `RESOLVES_TO` edge carries: `confidence`, `method`, `resolved_at`, `resolver_version`.

---

## Consequences

### What changes in extraction

Each connector (code, Notion, Slack, Salesforce) extracts source artifacts as before. A new resolution pass runs after extraction:

1. For each new source artifact, run the resolution tiers in priority order
2. If match found above threshold: create/update the domain entity; add `RESOLVES_TO` edge
3. If no match: leave artifact as an unresolved source entity (still queryable; just not cross-source)
4. Resolution runs incrementally (only new/changed artifacts; not full-corpus re-run)

### What changes in query

Query at domain level: `GET /query?q=tell+me+about+Customer&entity_scope=domain`
- Retrieves the domain entity (`domain://customer@workspace`)
- Follows `RESOLVES_TO` edges to all linked source artifacts
- Surfaces evidence from each source; answers reference multiple sources
- Citations list includes source-type diversity (code + doc + slack)

Query at source level (default, backwards-compatible): unchanged.

### What changes in persona templates

Persona templates (ADR-0079) gain a `domain_entity_scope` field:
```yaml
# developer persona, blast_radius template
scope: domain://payments-service@workspace  # answers span all sources for this entity
```

### What does NOT change (non-goals)

- Extraction pipeline internals (each connector is source-specific)
- Storage backend (Qdrant, Postgres, JSON brain — unchanged)
- Existing code entity URNs (`code://...`) — no migration
- Multi-repo federation (that's Wave A2 SCIP + ADR-0093 scope; not this ADR)
- Resolution across workspaces (scoped to single workspace in P1)

---

## Open questions (defer to ADR-0093 implementation)

1. **Resolution throughput**: How fast can we resolve 100K entities from a new Notion ingestion? Need batch embedding support.
2. **Stale resolution**: If the Java class is refactored but the Notion page isn't updated, when does the resolution edge expire?
3. **Ambiguous entities**: "Order" exists in 3 different domain senses (Sales Order, Work Order, Sort Order). Needs disambiguation UI.

---

## Alternatives considered

**Alternative: Source-level queries with fan-out** — keep artifacts separate; at query time fan out to each source and merge. Rejected because: fan-out latency is O(N sources); no unified entity identity; citations are source-level not domain-level; persona templates can't express cross-source shapes cleanly.

**Alternative: One brain per source** — separate brain per connector. Rejected because: cross-source queries impossible; persona answers can't synthesise; the whole V2 value proposition requires a unified graph.

---

## Implementation sequence

1. **This ADR (B1.1)**: framing + schema + policy (writing only)
2. **B1.2 ADR-0092**: Multi-Source Connector Framework — defines the connector interface that all non-code connectors implement; resolution hooks defined here
3. **B1.3 ADR-0093**: Cross-Source Entity Resolution P1 — implements the resolution tiers, domain entity materialisation, `RESOLVES_TO` edge creation
4. **B1.4**: Notion connector (first real connector built on B1.2 + B1.3)
5. **Wave B2**: Slack, Confluence, Salesforce connectors + cross-source persona answers
