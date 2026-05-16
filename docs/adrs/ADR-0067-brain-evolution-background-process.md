# ADR-0067 — Brain Evolution Background Process (auto-link, consolidate, prune)

**Status:** Proposed
**Date:** 2026-05-11
**Inspired by:** ContextDB's `dynamics/evolution.py` (Apache 2.0; pattern adopted, code is our own per LEGAL-CONTEXTDB-INTEGRATION.md)
**Sequenced with:** depends on ADR-0066 (PatternUtility for pruning decisions), ADR-0064 (audit log for tracking what was pruned). Otherwise parallel-shippable.

---

## Context

After multiple `brain index` runs against a repo, the brain bloats:

- **Stale entities accumulate**: methods that were renamed have both old and new versions; deleted methods linger.
- **Duplicate Patterns**: ADR-0055's idiom detector finds the same shape in multiple runs and emits Pattern entities for each — dozens of "defensive_filter_copy" patterns where one would do.
- **Hallucinated entities never get cleaned**: ADR-0056 marks them `verified="hallucinated"` but they sit in Postgres forever.
- **Embedding similarity edges aren't auto-maintained**: when entity A and B are 95% semantically similar, no SIMILAR_TO edge exists unless something explicitly created it.
- **Useless Patterns linger**: ADR-0066 PatternUtility tracks usage; nothing ACTS on it.

ContextDB ships `dynamics/evolution.py` — a continuous background process that runs three operations:
1. **Auto-link**: as new memories arrive, add edges to graphs (semantic, temporal, etc.)
2. **Consolidate**: merge dense semantic clusters into a single representative
3. **Prune**: drop stale / redundant entries per policy

The brain needs the same. Without it, every long-running customer's brain becomes a bloated mess within 6 months of `brain index` runs. **Operational hygiene that pays off only after the brain has been deployed for months — but if you don't ship it, you'll wish you had.**

---

## Decision

A scheduled job (`pipeline/brain_evolution.py`) that runs every 24 hours per workspace. Three sub-passes, each independently configurable:

### M1 — Auto-link

Scans entities created/modified in the last 24 hours. For each:

1. **Semantic neighbours**: compute embedding (already exists post-extraction); query Qdrant for top-K nearest at distance < 0.15; emit `SIMILAR_TO` edges.
2. **Temporal cluster**: cluster by `last_modified_commit` proximity (commits within same PR / same day); emit `CO_MODIFIED_WITH` edges.
3. **Domain reinforcement**: re-run ADR-0059's domain-inference for new entities; if they fit an existing DomainEntity (>0.8 cosine to its anchor), add `REPRESENTS` edge.

Cost: $0 (uses existing embeddings) for layers 1-2; ~$0.001 per new domain match (LLM call). Per-workspace cost: ~$0.05 per evolution cycle.

### M2 — Consolidate

Identifies duplicate / overlapping entities and merges them.

**Rules**:
- Two `Pattern` entities with identical `name` and overlapping `instances` (>50% overlap) → merge. Increment `instance_count`; preserve all instance URNs.
- Two `DomainEntity` entities with overlapping `aliases` and similar descriptions → merge. The newer one wins on `description`; aliases unioned; anchor classes unioned.
- Two `SharedInvariant` entities with identical `statement` → merge.
- Two `Method` entities with same `qname` and same `body_hash` but different URNs (a bug, but we tolerate it) → merge; preserve all variants under `historical_versions[]`.

**Audit trail**: every merge writes an audit event (per ADR-0064) recording what was merged into what + `merge_strategy`. Ensures provenance survives consolidation.

### M3 — Prune

Drops entities by policy:

- **Hallucinated AND no inbound edges AND no human override**: drop after 30 days. (Per ADR-0056 verifier outputs, ADR-0063 provenance.)
- **Pattern with `surfaced_count == 0` AND created > 30 days ago**: drop. (Per ADR-0066 PatternUtility — patterns nobody uses are noise.)
- **Entity whose `expires_at` has passed**: drop. (Per ADR-0064 retention policy.)
- **Duplicate `historical_version` entries beyond N=5 per qname**: drop oldest. (Brain doesn't need 100 historical versions of the same method.)
- **Orphan structural entities** (no inbound or outbound edges, low confidence, > 60 days old): drop. (Cleanup of leftover drift entities from broken extractions.)

**Audit trail**: every prune writes `action=delete` audit event with the entity's prior state in the `before` field.

### Configuration & opt-out

Each sub-pass has an env-flag override per workspace (`BRAIN_EVOLUTION_AUTOLINK=false` etc.). Customers concerned about silent state changes can disable consolidation; pruning is the most likely candidate to disable in regulated environments where data must be immutable for N years.

For regulated retention: the V16 audit table from ADR-0064 always preserves the deletion record even when entity is pruned.

---

## File ownership for THIS PR (parallel-safe)

```
company-brain-ai/src/companybrain/pipeline/brain_evolution.py          # NEW — orchestrator
company-brain-ai/src/companybrain/pipeline/auto_linker.py              # NEW — M1
company-brain-ai/src/companybrain/pipeline/consolidator.py             # NEW — M2
company-brain-ai/src/companybrain/pipeline/pruner.py                   # NEW — M3
company-brain-ai/src/companybrain/pipeline/evolution_scheduler.py      # NEW — APScheduler trigger; uses ADR-0052 P6 scheduler primitive if shipped
tests/unit/test_auto_linker.py
tests/unit/test_consolidator.py
tests/unit/test_pruner.py
tests/acceptance/test_evolution_cycle.py
```

Append-only edits to:

```
company-brain-ai/src/companybrain/config.py    # add evolution_enabled, evolution_interval_hours, per-pass flags
company-brain-ai/src/companybrain/cli.py       # add `brain evolution run` (manual trigger) + `brain evolution status`
```

Does NOT touch any file owned by ADR-0055-0066 OR 0068-0069.

---

## Acceptance test

```python
async def test_auto_linker_emits_similar_to_edges():
    """Two methods with 0.92 cosine similarity get a SIMILAR_TO edge after evolution."""
    await create_entity(name="A", embedding=embedding_a)
    await create_entity(name="B", embedding=embedding_b)  # 0.92 cosine to A
    await run_evolution_cycle()
    edges = await query_edges(from_urn=urn_a, edge_type="SIMILAR_TO")
    assert any(e.to_urn == urn_b for e in edges)


async def test_consolidator_merges_duplicate_patterns():
    """Two Pattern entities with the same name + 60% overlapping instances merge into one."""
    await create_pattern(name="defensive_filter_copy", instances=["X", "Y", "Z"])
    await create_pattern(name="defensive_filter_copy", instances=["Y", "Z", "W", "V"])
    await run_evolution_cycle()
    patterns = await query_patterns(name="defensive_filter_copy")
    assert len(patterns) == 1
    assert set(patterns[0].instances) == {"X", "Y", "Z", "W", "V"}


async def test_pruner_drops_hallucinated_after_30_days():
    """Hallucinated entity with no inbound edges and 31 days old is dropped."""
    await create_entity(verified="hallucinated", created_days_ago=31, has_inbound_edges=False)
    await run_evolution_cycle()
    assert await entity_exists(...) is False
    # Audit trail preserved
    audit = await audit_query(action="delete", since="-1h")
    assert any("hallucinated" in str(a.before) for a in audit)


async def test_pruner_skips_human_override():
    """Hallucinated entity that someone marked as 'human_brain_md' provenance is NEVER pruned."""
    await create_entity(verified="hallucinated", provenance={"purpose": {"source": "human_brain_md"}})
    await advance_clock(days=365)
    await run_evolution_cycle()
    assert await entity_exists(...) is True


async def test_evolution_writes_audit_for_every_action():
    await run_evolution_cycle()
    audit = await audit_query(actor="evolution_scheduler", since="-1h")
    assert len(audit) > 0
    assert all(a.action in ("update", "delete") for a in audit)
```

---

## Effort estimate

2 days, easily parallelisable to 1 day with 2 sessions:

| Workstream | Days |
|---|---|
| Auto-linker (M1) + Consolidator (M2) + Pruner (M3) | 1.5 |
| Scheduler + CLI commands + tests | 0.5 |

---

## Action items

1. [ ] `pipeline/auto_linker.py` — Qdrant similarity-search + SIMILAR_TO edge emission.
2. [ ] `pipeline/consolidator.py` — pattern + domain + invariant merging with provenance preservation.
3. [ ] `pipeline/pruner.py` — 5-policy prune with audit-trail writes.
4. [ ] `pipeline/evolution_scheduler.py` — APScheduler integration; one cycle per workspace per 24h.
5. [ ] `pipeline/brain_evolution.py` — orchestrator: M1 → M2 → M3 in sequence.
6. [ ] `brain evolution run` CLI command — manual trigger; useful for testing + on-demand cleanup.
7. [ ] `brain evolution status` CLI command — last cycle stats; opt-out flags.
8. [ ] Acceptance: 5 tests above PASS.
9. [ ] Telemetry: per-cycle `auto_linked_edges`, `consolidated_count`, `pruned_count`, `audit_events_written`.
10. [ ] Configuration in `.env`: `BRAIN_EVOLUTION_ENABLED=true|false`, `BRAIN_EVOLUTION_AUTOLINK=true`, `BRAIN_EVOLUTION_CONSOLIDATE=true`, `BRAIN_EVOLUTION_PRUNE=true`, `BRAIN_EVOLUTION_INTERVAL_HOURS=24`.
