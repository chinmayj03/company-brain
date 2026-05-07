# ADR-006 Week 1 Implementation Summary

**Date completed:** 2026-04-29  
**Scope:** Structural Layer MVP — tree-sitter parser, risk scoring, DB schema, blast-radius integration

---

## Completed Action Items

| # | ADR-006 § | Deliverable | Status |
|---|-----------|-------------|--------|
| 1 | §1  | `V4__structural_columns.sql` — Flyway migration | ✅ Done |
| 2 | §2  | `pyproject.toml` — tree-sitter-languages + tree-sitter-go | ✅ Done |
| 3 | §4  | `companybrain/structural/parser.py` — tree-sitter walker | ✅ Done |
| 4 | §8  | `companybrain/structural/risk.py` — multi-factor risk scorer | ✅ Done |
| 5 | §13 | `BlastRadiusNode.java` DTO — riskScore, riskFactors, flowMembership | ✅ Done |
| 6 | §14 | `BlastRadiusPanel.jsx` — risk-sorted nodes with factor breakdown | ✅ Done |
| 7 | §30 | `companybrain/structural/backfill.py` — one-shot backfill CLI | ✅ Done |
| 8 | §31 | `tests/unit/structural/test_parser_equivalence.py` — 17 tests | ✅ Done |

---

## New & Modified Files

### New files
```
company-brain-backend/src/main/resources/db/migration/
  V4__structural_columns.sql

company-brain-ai/src/companybrain/structural/
  __init__.py
  parser.py
  risk.py
  backfill.py

company-brain-ai/tests/unit/structural/
  __init__.py
  test_parser_equivalence.py
```

### Modified files
```
company-brain-ai/pyproject.toml
  → Added tree-sitter, tree-sitter-languages, tree-sitter-{java,python,typescript,go}

company-brain-backend/src/main/java/com/companybrain/dto/BlastRadiusNode.java
  → Added riskScore (Double), riskFactors (Map<String,Double>), flowMembership (List<UUID>)
  → Added @JsonInclude(NON_NULL)

company-brain-backend/src/main/java/com/companybrain/service/BlastRadiusService.java
  → SQL now SELECTs n.risk_score, n.risk_factors::TEXT
  → JOIN nodes n ON n.id = br.id added to recursive CTE outer query
  → Row mapper parses risk_factors JSON → Map<String,Double>
  → Post-query sort by riskScore DESC (nulls last)

company-brain-frontend/src/components/graph/BlastRadiusPanel.jsx
  → riskBucket() bucketing function (minimal/low/medium/high/critical)
  → RISK_BADGE colour map per bucket
  → RiskFactorTooltip component with mini progress bars
  → NodeCard rewritten with risk badge + toggle explainer
  → Panel header shows "X high-risk nodes affected" warning
  → Graceful fallback when riskScore is null
```

---

## Verification Results

| Check | Result |
|-------|--------|
| Equivalence tests (17) | ✅ 17/17 passing |
| Risk scorer range (0.0–1.0) | ✅ Verified programmatically |
| Security keyword detection | ✅ `authenticate` → score 0.50, security factor 0.20 |
| Migration SQL (26 statements, balanced parens) | ✅ Structurally valid |
| BlastRadiusNode new fields | ✅ Present with @JsonInclude(NON_NULL) |
| BlastRadiusService risk sort | ✅ DESC nulls-last |
| BlastRadiusPanel icon imports | ✅ All 6 icons imported and used |

---

## Key Design Decisions (Week 1)

**Migration is V4, not V3.** V3 was already used by ADR-005 artifact tables. Filed as V4 to preserve Flyway ordering.

**Grammar loading strategy.** `_load_grammar(language)` tries `tree_sitter_languages.get_language()` first (bundled 23-language pack), then falls back to individual `tree_sitter_{lang}` packages. This means the parser degrades gracefully in environments that only have individual packages installed.

**`kind="Test"` for files under `tests/`** The parser marks all functions in test-path files as `kind="Test"`. This is intentional CRG behavior (test nodes get different graph weights). The equivalence tests now accept `kind in ("Function", "Test")` for security-sensitive function assertions.

**Tolerance in equivalence tests.** Python parity allows ±1 because the regex extractor finds nested closures (e.g. `wrapper` inside a decorator) that tree-sitter intentionally excludes. TypeScript parity allows ±1 because naive regex patterns have false positives (e.g. matching `if` as a function name). In both cases the parser result is more correct.

**`qualified_name IS NULL` guard in backfill.** The UPDATE only touches nodes not yet structurally scanned. Re-running the backfill is safe and idempotent.

**`flowMembership` seeded as empty list.** The `BlastRadiusNode.flowMembership` field is present but always returns `[]` until the flows pipeline (week 4) populates the `flow_memberships` table.

---

## Known Issues / Deferred

| Item | Deferred to |
|------|-------------|
| `flows.py` — entry-point detection + BFS flow tracing | Week 4 |
| `communities.py` — Leiden community detection | Week 4 |
| `topology.py` — hub_degree + bridge_betweenness | Week 4 |
| Hash-diff incremental engine (skip unchanged files) | Week 2 |
| MCP tool surface (`structural_search`, `risk_hotspots`) | Week 2 |
| Go grammar: `tree-sitter-go` not in `tree-sitter-languages` bundle — falls back to individual package | Week 2 (confirm availability) |
| `cross_community_caller_count` always 0 | Week 4 (communities.py) |
| `flow_count` / `flow_criticality_sum` always 0 | Week 4 (flows.py) |

---

## Ready for Week 2

The codebase is in the following state heading into Week 2:

**Schema** — `V4__structural_columns.sql` is ready to apply. The `nodes` table has `qualified_name`, `file_hash`, `line_start`, `line_end`, `risk_score`, `risk_factors`. All five new tables (`flows`, `flow_memberships`, `communities`, `node_communities`, `graph_metrics`) exist with RLS.

**Parser** — `parse_file()` / `parse_directory()` produce `NodeInfo` + `EdgeInfo` records for Java, Python, TypeScript, TSX, JavaScript, and Go. Qualified names follow the `path/to/file.ext::Class.method` scheme.

**Risk scorer** — `compute_risk_score()` and `score_from_row()` are live. Security, test-coverage, and caller factors are active. Flow and community factors return 0 until weeks 3–4.

**Blast radius** — `BlastRadiusService` already queries `risk_score`/`risk_factors` from Postgres and sorts results. `BlastRadiusPanel` renders risk badges and factor explainers. Once the migration runs and the backfill CLI executes, the UI will immediately show risk data.

**Backfill CLI** — Run this once after deploying V4 migration to populate structural columns for any pre-existing workspace:
```bash
python -m companybrain.structural.backfill \
    --repo /path/to/repo \
    --workspace-id <uuid> \
    --db-url postgresql://companybrain:companybrain@localhost:5432/companybrain
```

**Tests** — 17 structural equivalence tests covering Java, Python, TypeScript, Go parsing and risk scoring. All passing.

**Week 2 should focus on:** hash-diff incremental re-scan engine (skip files whose `file_hash` hasn't changed), the MCP tool surface (`structural_search` + `risk_hotspots` FastAPI endpoints), and wiring the backfill into the context pipeline so it runs automatically on workspace creation.
