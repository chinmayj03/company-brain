# ADR-0083 — Catalog Evolution & Curation

**Status:** Proposed
**Date:** 2026-05-13
**Builds on:** ADR-0059 (domain inference), ADR-0066 (experiential memory), ADR-0067 (brain evolution), ADR-0072 (memory primitives)
**Pairs with:** ADR-0079 (templates as catalog entries), ADR-0084 (compression/expansion shares state)
**Strategic goal:** the brain's entity catalog and template catalog are both living, evolving artifacts. This ADR defines how they evolve safely — promotion thresholds, curation UI, snapshot/rollback, and vertical-pack distribution.

---

## Context

Two prior docs established the framing:

- **ENTITY-AND-QUERY-REFINEMENT-LOOP**: brain ships with a vertical-specific seed catalog (Healthcare = Payer/Provider/Plan/Claim/Member; SaaS = Customer/Subscription/Feature; etc.) and discovers more from corpus. Discoveries get promoted to first-class on signal.
- **PERSONA-DRIVEN-EXTRACTION**: query templates ship as a seed (~6-10 per persona) and refine from usage signal.

Both are **catalogs that evolve**. Without governance, they degrade fast: false merges destroy data, junk discoveries crowd out real ones, abandoned templates accumulate, vertical packs go stale.

This ADR is the **governance layer**. It defines:
- Promotion / demotion / merge / archive rules (objective + thresholds)
- Curation surface for human admins (review queue, override, force-merge)
- Versioning + rollback (catalog snapshot every change; restore on demand)
- Vertical-pack format (so we can ship and update Healthcare/SaaS/Fintech packs independently)
- Audit log (every catalog change, who/what/why/when, hash-chained per ADR-0064)

It is the runtime/admin counterpart to ADR-0084 (which handles the data-side compaction).

---

## Decision

Six mechanisms.

---

### M1 — Promotion / Demotion / Merge Rules

**Problem solved**: catalog needs deterministic, auditable transitions for entities and templates.

**Mechanism**: each catalog entry (entity OR template) has a state machine:

```
proposed → candidate → promoted → demoted → archived → tombstoned
              ↑                       ↓
              └────── re-promoted ────┘
```

**Entity transitions**:

| From → To | Trigger | Auto vs Human |
|---|---|---|
| proposed → candidate | Inferred from corpus by ADR-0059, evidence_count ≥ 3 | Auto |
| candidate → promoted | evidence_count ≥ 20 AND mentioned in ≥ 2 source types AND queried by user ≥ 1 | Auto if confidence > 0.85; queue otherwise |
| promoted → demoted | last_useful_at older than 90 days | Auto |
| demoted → archived | last_useful_at older than 180 days | Auto |
| archived → tombstoned | last_useful_at older than 365 days OR explicit human archive | Human-confirm |
| any → merged_into(other) | High alias overlap (>0.92 semantic + >0.7 structural co-occurrence) | Auto if low-salience; human for high-salience |
| any → re-promoted | New evidence after demotion/archive resurfaces it | Auto |

**Template transitions** (from ADR-0079):

| From → To | Trigger |
|---|---|
| proposed → candidate | Author drafted in admin UI |
| candidate → promoted | Used ≥ N times with thumbs-up rate ≥ 0.7 |
| promoted → demoted | Match-confidence average drops below 0.5 OR usage drops |
| demoted → archived | Unused ≥ 60 days after demotion |

All thresholds are **workspace-configurable**. Defaults shipped as conservative.

---

### M2 — Curation UI / API

**Problem solved**: humans need a safe, fast surface to inspect, override, merge, and approve catalog changes.

**Mechanism**: web UI + parallel API. Three primary views.

**View 1 — Curation Queue**: pending changes awaiting human approval. Each item shows:
- Proposal type (promote, merge, demote, archive, new-template)
- Affected entity/template
- Evidence summary (why the brain proposed it)
- One-click Approve / Reject / Modify-and-approve / Defer (review later)
- Bulk actions for low-stakes batches

**View 2 — Catalog Browser**: searchable list of all entities and templates. Filter by state, source, salience, last-used. Each entry has full history (state transitions, evidence accumulation, edge changes). Inline edit for canonical_name, aliases, description, salience override.

**View 3 — Audit Log Viewer**: every catalog change with who/what/why/when. Filter by actor, time range, entity, change type. Export for compliance.

API mirrors UI: `POST /catalog/entities/{id}/promote`, `POST /catalog/entities/{id}/merge_into/{target_id}`, `GET /catalog/queue`, etc. Same auth model as the rest of the brain (workspace-scoped + role-based).

---

### M3 — Snapshot & Rollback

**Problem solved**: a bad auto-promotion or false merge can poison the brain. Need fast, safe rollback.

**Mechanism**: catalog state is snapshotted on every transition (via the audit log) and on a daily checkpoint:

```python
@dataclass
class CatalogSnapshot:
    snapshot_id: str
    snapshot_at: datetime
    workspace: str
    type: Literal["transactional", "daily", "manual"]
    triggering_change: Optional[CatalogChange]
    entity_count: int
    template_count: int
    blob_ref: str             # storage location of full catalog dump
    parent_snapshot_id: Optional[str]
    rollback_safety: Literal["safe", "lossy", "destructive"]
```

`rollback_safety` reflects whether re-applying queries since the snapshot would still produce identical results. `safe` = yes; `lossy` = some user feedback would be lost; `destructive` = some downstream data depends on this state and rolling back would corrupt it.

**Rollback**: admin UI shows snapshots; selecting one restores catalog state to that point. Audit log records the rollback as an event (rollbacks themselves are audited).

Storage: snapshots are append-only; never deleted (configurable retention, default 365 days). Compressed delta encoding to keep storage cheap.

---

### M4 — Vertical-Pack Format

**Problem solved**: shipping a new vertical (Healthcare-RCM, Fintech-Lending, Marketplace) shouldn't require a code release. Packs should be installable, versioned, updatable.

**Mechanism**: a vertical pack is a versioned bundle:

```
healthcare-rcm-v1.3.0.pack/
├── pack.yaml                    # metadata (name, version, description, deps)
├── entities/
│   ├── seed.yaml                # spine entities
│   ├── inference_hints.yaml     # patterns to help discovery layer
│   └── edge_types.yaml          # vertical-specific edge type definitions
├── templates/
│   ├── pm.yaml
│   ├── developer.yaml
│   ├── cs.yaml
│   ├── vp_eng.yaml
│   ├── cfo.yaml
│   └── ceo.yaml
├── bindings/
│   ├── pm-bindings.yaml         # how PM templates fill against seed entities
│   └── ... (one per persona)
├── connectors/
│   └── recommended.yaml         # which connectors are most useful for this vertical
├── changelog.md
└── tests/                       # golden-set queries that should work after install
```

Pack lifecycle:
- **Install**: validates schema, runs golden tests, applies as seed catalog overlay
- **Update**: diff against current pack version; surfaces breaking changes; admin approves
- **Uninstall**: removes pack-provided entities/templates not promoted by user usage; preserves anything user-promoted (can't accidentally lose customer-discovered entities)
- **Fork**: customer can fork a pack into a workspace-private version for heavy customization

Packs distributed via simple registry (hosted manifest + signed bundles). Customers can host private registries for proprietary verticals.

---

### M5 — High-Salience Auto-Merge Guardrails

**Problem solved**: false merges of high-salience entities are destructive. Need stronger protection there.

**Mechanism**: merge proposals are gated by a salience threshold:

| Combined salience | Merge handling |
|---|---|
| Both entities < 0.3 | Auto-merge if confidence > 0.85 |
| Either entity 0.3-0.7 | Queue for human review; high-priority |
| Either entity > 0.7 | Always require human approval; bring affected edges and evidence into the review screen |
| Entities span source types (e.g., Salesforce account + code class) | Always human-approve |

For high-salience merges, the curation UI surfaces:
- Side-by-side card comparison
- Edge graph diff
- Evidence sample (10 mentions of each)
- Risk flag: "merging would affect N downstream queries / dashboards"

Merge is reversible (snapshot before; rollback supported).

---

### M6 — Hash-Chained Audit Log

**Problem solved**: customers in regulated verticals need provable audit trail of catalog changes.

**Mechanism**: extends ADR-0064 (privacy and audit layer) hash-chained log to catalog changes. Every catalog mutation:

```python
@dataclass
class CatalogChange:
    change_id: str
    timestamp: datetime
    actor: ActorRef                # user/service-account/auto-system
    workspace: str
    change_type: Literal[
        "entity_proposed", "entity_promoted", "entity_demoted",
        "entity_merged", "entity_archived", "entity_tombstoned",
        "template_added", "template_promoted", "template_demoted", "template_edited",
        "binding_updated", "pack_installed", "pack_updated", "pack_uninstalled",
        "rollback_applied", "snapshot_created",
    ]
    target_urn: str
    before_state: Optional[dict]   # for diffs
    after_state: Optional[dict]
    rationale: Optional[str]       # human-provided reason for high-salience changes
    evidence_refs: list[str]       # supporting evidence (for auto-changes)
    prev_change_hash: str
    self_hash: str                 # hash(content + prev_change_hash)
```

Append-only. Tamper-evident (any modification breaks the chain). Exportable for compliance (SOC2, HIPAA, etc.).

For high-salience changes, `rationale` is required — surfaced in the UI as a mandatory field before submission.

---

## Consequences

**Positive**:
- Catalog stays clean and trusted as the brain matures
- Vertical packs make shipping new industries cheap (weeks, not months)
- Snapshot/rollback de-risks aggressive auto-promotion
- Audit log unlocks regulated-industry sales (healthcare, fintech, gov)
- Curation queue creates a structured admin workflow that customer-success can train on

**Negative / risks**:
- Curation UI is real product surface — needs design polish, not just a JSON dump
- Vertical-pack tooling is yet-another-thing-to-ship; tempting to skip in seed window
- Audit log storage grows unboundedly without retention policy + compression
- High-salience merge guardrails create human bottleneck if many proposals queue up; need batch-review flows
- Pack updates that change template shape can break existing customer queries; need careful migration tooling

**Cost estimate**:
- M1 promotion rules + state machines: 1 week
- M2 curation UI (admin) + API: 2 weeks (requires real frontend work)
- M3 snapshot + rollback: 0.5 week
- M4 vertical-pack format + install tooling: 1.5 weeks
- M5 high-salience guardrails + diff UI: 0.5 week (extends M2)
- M6 audit log integration with ADR-0064: 0.5 week

**Total: 5-6 weeks**. Two engineers (one backend, one frontend); parallel-safe with other ADRs once the schemas (M1) are agreed.

---

## Phasing

**Phase 1 (seed)**: M1 state machines, M3 snapshot/rollback, M6 audit log integration, M4 minimal pack format (load from YAML, no registry yet). M2 starts as JSON-API only — no UI.

**Phase 2 (seed → Series A)**: M2 full curation UI for admins, M4 pack registry + install/update tooling, M5 high-salience guardrails with diff UI.

**Phase 3 (Series A and after)**: customer-facing pack marketplace, fork/diff workflow, multi-workspace pack management for enterprise tenants, automated pack-update migration suggestions.

---

## Open questions

1. **Who owns curation in a customer org?** Default: workspace admin role. For larger orgs, RBAC with curator role separate from admin. Out of scope for Phase 1.
2. **Rollback granularity**: full catalog snapshot vs per-change reversal? Default: per-change reversal where possible (cheaper); full snapshot when not (e.g., merging changes propagate to many entities — easier to roll back to a snapshot).
3. **Pack conflicts**: what if a customer installs two packs with overlapping entities (e.g., a generic SaaS pack and a healthcare pack)? Default: union with last-installed precedence; admin can pin per-entity overrides.
4. **Pack signing / trust**: how do customers verify a pack hasn't been tampered with? Default: signed manifests (Ed25519); bundled signature; install rejects unsigned packs unless admin explicitly opts in.
5. **What about the experiential-memory data (ADR-0066) — does it get versioned with catalog snapshots?** No. Experiential memory is append-only and survives rollbacks. Catalog rollback restores schema/templates; doesn't erase signal.
6. **Pack upgrade UX** when a template shape changes: shape-evolution detector warns "this update changes the structure of `pm.feature_progress`; X open queries will be affected." Phase 2 work.

---

## What this unlocks

- ADR-0079 templates can evolve safely (promotion/demotion under M1)
- ADR-0084 compression cards can be regenerated without losing user-curated overrides (M3 snapshot)
- New vertical launches become a packaging exercise, not an engineering project (M4)
- Regulated-industry sales (healthcare, fintech, gov) have audit story (M6)
- Customer success has a real workflow surface (M2)

This ADR is the **governance plane**. Without it, the brain's other learning systems silently corrupt themselves over months.
