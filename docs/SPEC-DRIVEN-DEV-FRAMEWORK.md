# Spec-Driven Development Framework

**Source pattern**: addyosmani/agent-skills `spec-driven-development` skill + karpathy-skills `goal-driven execution` + `surgical changes` principles. Applied to (a) brain V2 engineering and (b) every frontend feature going forward.

**Why**: features die in three predictable ways — built without a clear spec ("seemed obvious"), built without a plan ("we'll figure it out"), built without acceptance gates ("looked right, shipped"). Spec-driven dev forces explicit gates before code is written.

---

## The four-gate flow

```
SPECIFY  →  PLAN  →  TASKS  →  IMPLEMENT
   │          │         │           │
   │          │         │           └─ ACCEPTANCE GATE
   │          │         │              (tests + evidence + reviewer sign-off)
   │          │         │
   │          │         └─ BREAKDOWN GATE
   │          │            (every task ≤ 0.5 day, each has acceptance criteria)
   │          │
   │          └─ DESIGN GATE
   │             (architecture review + dependency check + risk list)
   │
   └─ INTENT GATE
      (user need + success criteria written; reviewer confirms problem is real)
```

A feature cannot pass a gate without producing a specific artifact reviewed by a second person.

---

## Gate 1 — SPECIFY (intent + success criteria)

**Artifact**: `specs/<feature-slug>/00-spec.md`

**Contents** (mandatory sections):

```markdown
# Spec — <feature name>

## Problem (one paragraph)
What user is hurting? Why is this worth doing now?

## User outcomes
Bullet list: what does the user be able to do AFTER this ships
that they CAN'T do today? Be specific, not hand-wavy.

## Non-goals (mandatory)
What this feature explicitly will NOT do. Surfaces hidden assumptions
(per karpathy-skills "surface assumptions" pattern).

## Success criteria (measurable)
- Quantitative: latency, accuracy, adoption rate, error rate
- Qualitative: 5 user quotes that would indicate success (write them now)

## Out-of-scope alternatives considered
What did we consider and reject? Why? (forces deliberate choice over default)

## Assumptions
- About users (persona, frequency, context of use)
- About data (volume, freshness, source)
- About system (load, integration, failure modes)

## Risks
What could make this not ship / not work / not be used?
```

**Gate criteria**:
- Written by feature owner
- Reviewed by one PM + one engineer
- Success criteria are numerically measurable OR explicitly justified as qualitative
- Non-goals section is non-empty

**Anti-rationalization** (paste into the spec template):

> "But it's obvious what this feature does." If it's obvious, writing the spec takes 20 minutes. If you can't write it in 20 minutes, it's not obvious.

---

## Gate 2 — PLAN (design + architecture review)

**Artifact**: `specs/<feature-slug>/01-plan.md`

**Contents**:

```markdown
# Plan — <feature name>

## Architecture
Diagram (ASCII or PNG) showing components touched, data flow,
dependencies.

## Touched surfaces
- Files / modules added or modified
- APIs added / changed
- Database schema changes (with migration plan)
- Frontend routes / components

## Dependencies
- Other in-flight ADRs / specs this depends on or blocks
- External services / libraries (version pinned)
- Connectors required

## Phasing
- Phase 1 ships X → demo-able outcome
- Phase 2 adds Y → ...
- (each phase has a checkpoint date estimate)

## Risk register
For each risk in the spec → mitigation strategy

## Rollback plan
If this ships and breaks something, how do we revert in <30 min?
```

**Gate criteria**:
- Reviewed by one senior engineer
- All dependencies confirmed available or sequenced
- Rollback plan is concrete (commit revert, feature flag toggle, etc.)
- Architecture review captures any cross-cutting concerns

**Karpathy-style "surgical changes" check**:
> "Are we changing anything we don't need to?" Every modified file must be justified in the touched-surfaces list. Drive-by refactors get rejected.

---

## Gate 3 — TASKS (breakdown into verifiable units)

**Artifact**: `specs/<feature-slug>/02-tasks.md`

**Contents**: numbered task list, each task:

```
### T1.3 — Wire glossary loader into prompt assembler

Owner: <name>
Estimated effort: 2-4 hours
Depends on: T1.1 (glossary store schema), T1.2 (workspace_id propagation)

Files touched:
- src/companybrain/prompts/assembler.py
- src/companybrain/workspace/tuning_store.py

Acceptance criteria:
- [ ] Unit test: glossary loaded for workspace X is in the prompt context
- [ ] Unit test: missing glossary falls back to global default without error
- [ ] Integration test: query with workspace_id returns response that uses a known glossary term
- [ ] No regression on existing prompt tests
- [ ] PR description includes the specific glossary term + expected use

Rollback: revert the prompts/assembler.py change; loader is no-op.
```

**Gate criteria**:
- Every task ≤ 0.5 day of effort (force decomposition; bigger tasks split further)
- Every task has explicit acceptance criteria
- Every task has explicit rollback (or "no-op" justification)
- Tasks are sequenced (depends-on links)
- Owner assigned per task

**Anti-rationalization**:
> "I'll write the tests later." No. A task without acceptance criteria isn't a task — it's a wish.

---

## Gate 4 — IMPLEMENT (with acceptance gate)

**Artifact**: PR linked to the spec; PR description matches the task acceptance criteria.

**Per-task workflow**:
1. Mark task in_progress (in TaskList or equivalent)
2. Write the failing test FIRST (TDD per addyosmani prove-it pattern + karpathy test-first principle)
3. Implement
4. Run the acceptance criteria; capture evidence
5. Self-review: did I touch anything not in the touched-surfaces list? (surgical-change check)
6. Open PR; reviewer verifies acceptance criteria against evidence
7. Merge; mark task complete

**Acceptance gate (per PR)**:
- All acceptance criteria boxes checked
- Evidence linked (test output, screenshot, log excerpt)
- No drift from the spec / plan (or explicit amendment recorded)
- Reviewer not the author
- Anti-rationalization check: PR description does not contain phrases "this should work", "I think this is right", "looks good to me" without supporting evidence

---

## Applied to brain V2 work

Every V2 component (each of the eight Wave-1 items) gets its own spec → plan → tasks → implementation.

Example: SQL deep extractor

- `specs/sql-deep-extractor/00-spec.md` — problem (80% of SQL missed), success (coverage > 75%, sqlglot lineage available), non-goals (not replacing schema discovery)
- `specs/sql-deep-extractor/01-plan.md` — architecture diagram, files touched, sqlglot/openlineage dependency added, rollback (feature flag SQL_DEEP_EXTRACTOR_ENABLED)
- `specs/sql-deep-extractor/02-tasks.md` — T1: sqlglot integration; T2: tree-sitter scan for embedded SQL; T3: JPA @Query pattern matcher; T4: jOOQ DSL pattern; T5: confidence tier downgrade for dynamic SQL; T6: golden-set tests
- PR per task; acceptance criteria measurable per task

---

## Applied to frontend features

Every frontend feature (drift dashboard, query console, brain browser, persona switcher, glossary admin UI, calibration dashboard) flows through the gates.

The frontend rebuild (ADR-0071) becomes the parent epic. Each screen is a feature with its own spec folder. Frontend specs add three sections:

```markdown
## Personas (which roles use this screen)
List ADR-0079 personas + use cases per persona

## Wireframes / mockups
Link to Figma frame or inline ASCII

## Data contract
What API endpoints; what response shape; degraded UI if data missing
```

---

## Spec folder layout

```
specs/
├── README.md                              # index of all specs
├── 2026-Q2-wave1-sql-deep-extractor/
│   ├── 00-spec.md
│   ├── 01-plan.md
│   ├── 02-tasks.md
│   └── 03-evidence/                       # PR links, test output, screenshots
├── 2026-Q2-wave1-hybrid-retrieval/
│   └── ...
├── 2026-Q2-frontend-drift-dashboard/
│   └── ...
└── _templates/
    ├── 00-spec.template.md
    ├── 01-plan.template.md
    └── 02-tasks.template.md
```

Each spec gets a date prefix + theme + feature slug for sortability.

---

## Anti-patterns (the spec-driven equivalent of code smells)

| Smell | Fix |
|---|---|
| Spec without success criteria | Reject at intent gate |
| Plan with no rollback | Reject at design gate |
| Tasks > 0.5 day | Split further |
| Task without acceptance criteria | Reject at breakdown gate |
| PR without evidence in description | Reject at acceptance gate |
| "Drive-by" refactors in PR | Move to separate PR with own spec |
| Spec amended mid-flight without checkpoint review | Pause; re-review; or split into new spec |
| Single-author + single-reviewer chain (same person both) | Block; require second pair of eyes |

---

## Lightweight version (for tiny features, < 1 day)

For genuinely tiny work (typo fix, version bump, copy edit), skip the spec folder and use a "lightweight spec" in the PR description:

```markdown
**Lightweight spec**
Problem: <one sentence>
Outcome: <one sentence>
Files touched: <list>
Acceptance: <checkbox list>
Rollback: <one sentence>
```

Anything ≥ 1 day of work uses the full four-gate flow.

---

## Tooling

Recommended (lightweight):
- `specs/` folder in repo, markdown files
- PRs link to spec folder
- Acceptance criteria as PR description checkboxes
- Engineering team has weekly "spec review" hour (15 min per spec under review)

Heavyweight options (for later, post-Series-A):
- Spec management tool (Linear specs, Productboard, Aha!)
- Auto-generate task tickets from spec tasks file

For now: markdown files in the repo are the tool.

---

## TL;DR

1. **Four gates: SPECIFY → PLAN → TASKS → IMPLEMENT.** Each gate produces a specific artifact reviewed by a second person.
2. **Each artifact has an anti-rationalization check** (spec must have non-goals; plan must have rollback; tasks must have acceptance criteria; PRs must have evidence).
3. **Applies to brain V2 work AND every frontend feature** going forward. The ADR-0071 frontend rebuild parent epic decomposes into per-screen specs.
4. **Lightweight version for < 1-day work** to avoid process overhead on trivial changes.
5. **Markdown files in `specs/` folder** — no new tooling needed.
6. **The discipline pays off in week 4**: features land predictably, fewer revisits, evidence-grounded confidence in what shipped.
