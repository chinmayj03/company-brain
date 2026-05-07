# Stage 1 Implementation Plan — Mono-Repo MVP

> **Goal.** Take the brain from "endpoint-scoped LLM extraction with dual-write to Postgres+Neo4j" to "repo-scoped, structural-first, JSON-SOT-backed, Qdrant-retrievable, MCP-queryable brain that works on one mono-repo and is ready to scale to many."
>
> **Audience.** A Sonnet-class agent picking up units of work. Each ADR below is sized for one focused session (1–5 days).
>
> **Companion to:** `MIGRATION-mono-to-multirepo-to-company.md` (strategy), `CURRENT-STATE-and-breakages.md` (audit).

---

## ADR Inventory and Dependency Graph

```
ADR-0011 ── structural-first extraction ordering
   │  (cb-api runs BEFORE Stage 1, not after Stage 5)
   │
   ├─► ADR-0012 ── BrainStore + .brain/ JSON SOT
   │      (events + JSON files; Postgres + Neo4j become consumers)
   │
   ├─► ADR-0013 ── Canonical URN identity
   │      (one ID scheme across Postgres + Neo4j + Qdrant)
   │       │
   │       ├─► ADR-0017 ── First-class assumption + business_context nodes
   │       │       (RELIES_ON edges; promote out of node_context)
   │       │
   │       └─► ADR-0015 ── Qdrant hybrid retriever (BM25S + voyage + RRF)
   │              (companybrain/retrieval/hybrid_search.py)
   │               │
   │               └─► ADR-0018 ── Smart-zone context assembler (T0/T1/T2)
   │                      (smart_zone.assemble(task, budget) -> Payload)
   │
   ├─► ADR-0014 ── Persistent L2 shared context
   │      (.brain/.l2-cache/{branch}.json across runs)
   │
   ├─► ADR-0016 ── Repo-scoped extraction trigger + brain CLI
   │      (brain index | map | query | blast-radius | rebuild-from-json)
   │
   └─► ADR-0019 ── MCP stdio server entry point
          (python -m companybrain.mcp.server; Claude Code attaches)
```

## Ordering — what to ship in which week

| Week | ADR | Title | Effort | Why now |
|---|---|---|---|---|
| 1 | **0011** | Structural-first extraction ordering | 1 d | Highest ROI fix; cuts LLM cost by 5–10× on incremental runs |
| 1 | **0012** | BrainStore + `.brain/` JSON SOT | 3 d | Foundation. Everything else writes through it. |
| 1 | **0013** | Canonical URN identity | 3 d | Unblocks cross-store joins. Required by 0015 / 0017. |
| 2 | **0014** | Persistent L2 shared context | 1 d | Cheap, immediate quality win on repeat runs |
| 2 | **0017** | First-class assumption + business_context | 2 d | Makes `RELIES_ON` edges meaningful for blast radius |
| 2 | **0016** | Repo-scoped CLI (`brain index`) | 3 d | Required by Stage 2 CI rebuild |
| 3 | **0015** | Qdrant hybrid retriever | 5 d | Unblocks 0018 |
| 4 | **0018** | Smart-zone context assembler | 5 d | Materializes T0/T1/T2 budgeting against the retriever |
| 4 | **0019** | MCP stdio server | 3 d | Claude Code integration; closes the loop on 0018 |
| **Total** | | | **~26 days** | One engineer, ~5 weeks calendar |

## Conventions used by every ADR

**File path conventions.**
- Python source: `company-brain-ai/src/companybrain/<module>/<file>.py`
- Java source: `company-brain-backend/src/main/java/com/companybrain/<package>/<File>.java`
- Flyway migrations: `company-brain-backend/src/main/resources/db/migration/V<n>__<name>.sql`
- TypeScript: `apps/<service>/src/<file>.ts`
- Tests (Python): `company-brain-ai/tests/unit/<module>/test_<file>.py`
- Tests (Java): `company-brain-backend/src/test/java/com/companybrain/<package>/<File>Test.java`

**Acceptance criteria style.** Every ADR ends with a checklist of testable assertions. The implementation is "done" when every check passes. Each check should map to a unit test or an integration test.

**Verification command style.** Each ADR provides shell commands (`make`, `psql`, `pytest`, `cypher-shell`, `qdrant-client`, `curl`) that prove the change works on the pilot repo.

**Code skeletons.** Where an ADR contains code, treat it as a contract — function names and signatures are specified, internals are guidance. The agent picking up the work owns the implementation details inside each function.

**No silent partial completion.** If an ADR has 4 acceptance criteria and the agent implements 3, the task is `in_progress`, not `completed`. Either close the gap or write a follow-up ADR for the remainder.

## Pre-flight checklist (before starting any ADR)

1. The pilot repo (the one mono-repo to validate against) is checked out at a known commit and committed to `repos.json` of the brain. Document the commit SHA.
2. `make up` brings Postgres + Neo4j + Qdrant + Redis + Ollama + LocalStack online; all green.
3. `OLLAMA_NUM_CTX=8192` set in `.env` (default 3072 truncates code).
4. `bun install` has run at the repo root and `bun run --cwd apps/api dev` starts cb-api on port 8090.
5. The current pipeline succeeds end-to-end on at least one known-good endpoint of the pilot repo (baseline before any change).
6. `git switch -c stage1-adr-<NNNN>-<slug>` for each ADR's branch.

Without all six green, do not start. The audits in `CURRENT-STATE-and-breakages.md` §7 are the playbook for fixing pre-flight failures.

## Per-ADR contract

Every ADR file follows this structure:

1. **Status / Date / Author / Supersedes** — frontmatter.
2. **Context** — what's broken / missing today, in 5–10 lines.
3. **Decision** — single sentence + 1 paragraph of justification.
4. **Implementation** — files to create, files to edit, schemas, code skeletons, migration SQL, dependency injections.
5. **Test plan** — exact pytest / JUnit / cypher tests.
6. **Acceptance criteria** — checklist; every box must tick.
7. **Verification commands** — shell commands a reviewer runs to confirm.
8. **Rollback** — exact steps to revert if the change breaks production.
9. **Out of scope** — what this ADR does NOT do, with a pointer to the follow-up ADR.

## What this plan deliberately does NOT do

- **Stage 2 (multi-repo federation).** Covered separately once Stage 1 is shipped end-to-end on the pilot.
- **Stage 3 (company-wide semantic).** Covered after Stage 2 stabilises in production.
- **Big refactors that span all ADRs.** Each ADR is independently shippable; cross-ADR refactors are explicitly out of scope.
- **Frontend changes.** The React UI keeps working unchanged — Postgres-mirror semantics in ADR-0012 preserve the existing read API.
- **Renaming existing types or files** unless an ADR explicitly says to. Rename in a follow-up after the migration stabilises.

## Where to read more

| File | Purpose |
|---|---|
| `MIGRATION-mono-to-multirepo-to-company.md` | Strategic three-stage roadmap |
| `CURRENT-STATE-and-breakages.md` | Honest audit of what works / breaks today |
| `harness-system-design.md` (root) | v1 harness target architecture |
| `ADR-001-enhanced-extraction-pipeline.md` (root) | Function-level + type-flow + state-slice extraction |
| `company-brain-v2-system-design.md` (root) | v2 living-brain target |
| `claude-code-architecture.md` (root) | Claude Code hook / MCP / skill integration |
| `docs/adrs/ADR-0001` … `ADR-0010` | Prior ADRs (URN scheme, graph storage, extractor plugin contract, etc.) |
| `docs/adrs/ADR-0011` … `ADR-0019` | This plan's ADRs |
