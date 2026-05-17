# Ship Log — Company Brain V2 Build

All items targeting `release/v2-seed-window` → `main` at Gate 1 (week 9).

| # | Sub-session | ADR | Branch | Commit | PR | Status |
|---|-------------|-----|--------|--------|----|--------|
| T1.1 | P0 Bug Bundle | — | `fix/p0-demo-bugs` | `ab03db0fb` | #68 | **MERGED** |
| T1.2 | ADR-0061 P1 Iterative Exploration | 0061 | — | — | — | In progress (ADR-0076 wired) |
| T1.3 | ADR-0073 Frontend Demo Live-Up | 0073 | — | — | — | Partial (frontend scaffold #80) |
| T1.4 | ADR-0072 Frontend Product Completion | 0072 | — | — | — | Partial (PRD-0001 added) |
| T1.5 | ADR-0074 Source Registry Pivot | 0074 | — | — | — | Pending |
| T1.6 | ADR-0075 UX Navigation Redesign | 0075 | — | — | — | Pending |
| T1.7 | ADR-0076 Frontend Rendering & Library | 0076 | `feature/adr-0076-frontend-rendering` | `9633b06da` | #79 | **MERGED** |
| T1.8 | ADR-0064 P1 Privacy + Audit | 0064 | `feature/adr-0064-p1-privacy-audit` | `27299bb26` | [#86](https://github.com/chinmayj03/company-brain/pull/86) | **PR OPEN** |
| T1.9 | ADR-0079 P1 Persona Templates | 0079 | `feature/adr-0079-p1-persona-templates` | `b4933d6d1` | [#87](https://github.com/chinmayj03/company-brain/pull/87) | **PR OPEN** |
| T1.10 | ADR-0082 P1 Drift Entity | 0082 | `feature/adr-0082-p1-drift-entity` | `579a07cf6` | [#83](https://github.com/chinmayj03/company-brain/pull/83) | **PR OPEN** |
| T1.11 | ADR-0090 P1 Event-Stream M1+M2 | 0090 | `feature/adr-0090-p1-event-stream` | `b8b274542` | [#88](https://github.com/chinmayj03/company-brain/pull/88) | **PR OPEN** |
| A1.1 | SQL Deep Extractor | — | `feature/v2-sql-deep-extractor` | `3a8e6c1ac` | [#82](https://github.com/chinmayj03/company-brain/pull/82) | **PR OPEN** |
| A1.2 | Hybrid Retrieval V2 | — | `feature/v2-hybrid-retrieval` | — | — | **RUNNING** |
| A1.3 | Prompt Caching | — | `feature/v2-prompt-caching` | — | — | **RUNNING** |
| A1.4 | Verbalized Confidence | — | `feature/v2-verbalized-confidence` | `5ceb2c792` | [#85](https://github.com/chinmayj03/company-brain/pull/85) | **PR OPEN** |
| A1.5 | Streaming + Parallel Retrieval | — | — | — | — | Pending |
| A1.6 | Glossary Auto-Discovery | — | — | — | — | Pending (blocked on T1.8) |
| A1.7 | Few-Shot Bank | — | — | — | — | Pending |
| A1.8 | Quality Regression Harness | — | — | — | — | Pending (blocked on A1.1 ✓, A1.2, A1.4) |
| B1.1 | ADR-0091 Domain-Entity-First | 0091 | `feature/adr-0091-domain-entity-first` | — | [#84](https://github.com/chinmayj03/company-brain/pull/84) | **PR OPEN** |
| B1.2 | ADR-0092 Connector Framework | 0092 | `feature/adr-0092-connector-framework` | `3bc879945` | — | **COMMITTED** (PR pending) |
| B1.3 | ADR-0093 Entity Resolution P1 | 0093 | `feature/adr-0093-entity-resolution` | — | — | **RUNNING** |
| B1.4 | Notion Connector | — | — | — | — | Pending (blocked on B1.2) |

## Key docs (now committed to main)
- `CLAUDE-CODE-TAKEOVER.md` — operating brief
- `docs/LEAN-PLAN-RECONCILED-WITH-EXISTING-ADRS.md` — 45 sub-sessions source of truth
- `docs/MASTER-ORCHESTRATOR-PROMPT.md` — DAG + parallelism map

## Gate 1 quality criteria (week 9)
- SQL coverage > 75% ✅ (A1.1 shipped)
- P50 query latency < 3s (A1.2 in progress)
- Citations ≥ 1 per answer (A1.4 shipped)
- PII detection ≥ 90% (T1.8 PR open)
- ≥ 1 non-code source live (B1.2/B1.4 in progress)
