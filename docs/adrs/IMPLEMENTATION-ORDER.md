# Implementation Order — ten Claude Code sessions for the full migration

This document lists every implementation prompt in dependency order so you
can run them in parallel where possible and sequentially where required.
Each session is a single PR; each PR is self-contained with its own
acceptance test.

---

## Total: 10 PRs / sessions

```
Session  Prompt file                                       Effort  Depends on
───────  ────────────────────────────────────────────────  ──────  ────────────────────
   1     SONNET-IMPLEMENTATION-PROMPT-ADR-0049.md          ~3d     —
   2     SONNET-IMPLEMENTATION-PROMPT-ADR-0048.md          ~3d     ADR-0049 merged*
   3     SONNET-IMPLEMENTATION-PROMPT-ADR-0050.md          ~3d     ADR-0048 merged*
   4     SONNET-IMPLEMENTATION-PROMPT-ADR-0051-P1.md       ~5d     ADR-0048+0049+0050 merged
   5     SONNET-IMPLEMENTATION-PROMPT-ADR-0051-P2.md       ~5d     P1 merged
   6     SONNET-IMPLEMENTATION-PROMPT-ADR-0051-P3.md       ~5d     P2 merged
   7     SONNET-IMPLEMENTATION-PROMPT-ADR-0051-P4.md       ~7d     P3 merged
   8     SONNET-IMPLEMENTATION-PROMPT-ADR-0052-P5.md       ~7d     P4 merged
   9     SONNET-IMPLEMENTATION-PROMPT-ADR-0052-P6.md       ~5d     P5 merged
  10     SONNET-IMPLEMENTATION-PROMPT-ADR-0052-P7.md       ~5d     P5 merged
                                                          ─────
                                                          ~48d (~10 weeks)

* Sessions 1-3 can ALSO be parallelised — the file-ownership table inside
  ADR-0050 §"Sequencing & Merge Plan" guarantees no merge conflicts even
  if 0048 lands before 0049, etc. The dependency arrows above are the
  IDEAL order; the harness benefits from caching being live first.
```

---

## Dependency graph

```
            ┌─────────┐
            │ ADR-0049│  (caching foundation — keystone)
            │ caching │
            └────┬────┘
                 │
                 ▼
            ┌─────────┐
            │ ADR-0048│  (two-agent extraction)
            │ 2-agent │
            └────┬────┘
                 │
                 ▼
            ┌─────────┐
            │ ADR-0050│  (big-repo recovery)
            │ big-repo│
            └────┬────┘
                 │
                 ▼
            ┌─────────┐
            │  P1     │  (HarnessLoop — the "rewrite the orchestrator" PR)
            │  loop   │
            └────┬────┘
                 │
                 ▼
            ┌─────────┐
            │  P2     │  (sub-agents — parallel fan-out)
            │ subagts │
            └────┬────┘
                 │
                 ▼
            ┌─────────┐
            │  P3     │  (skills + memory)
            │ skills  │
            └────┬────┘
                 │
                 ▼
            ┌─────────┐
            │  P4     │  (hooks/permissions/streaming/introspection)
            │ hooks   │
            └────┬────┘
                 │
                 ▼
            ┌─────────┐
            │  P5     │  (slash + MCP + workspace + headless + rooms)
            │ MCP     │
            └────┬────┘
                 ├──────────────────┐
                 ▼                  ▼
            ┌─────────┐        ┌─────────┐
            │  P6     │        │  P7     │  ← P6 and P7 can run in PARALLEL
            │ market  │        │  IDE    │     (different file sets, both need P5's MCP)
            │ +ecosys │        │ extn    │
            └─────────┘        └─────────┘
```

---

## How to launch each session

In a fresh Claude Code session for each PR:

```bash
cd /Users/chinmayjadhav/Documents/Claude/Projects/company-brain
claude code "Read docs/adrs/SONNET-IMPLEMENTATION-PROMPT-ADR-XXXX[-PN].md and land that PR end-to-end. Run the acceptance test before opening the PR. Use the exact PR description from the prompt."
```

Substitute the relevant prompt filename. The agent's pre-flight check
verifies prereqs are merged; if not, it stops with a clear error.

---

## Parallelisation opportunities

| Sessions | Can run in parallel? | Why |
|---|---|---|
| 1, 2, 3 (ADR-0049/0048/0050) | YES — the file-ownership table in ADR-0050 §"Sequencing & Merge Plan" guarantees no overlap. Land in any order; rebases are clean fast-forwards. | All three deliver building blocks that the harness later wraps. |
| 4 (P1) | NO — needs 1+2+3 | P1 wraps existing pipeline calls as harness tools; those calls need to be the new ADR-0048 / ADR-0050 versions. |
| 5–8 (P2 → P5) | NO — strict sequence | Each adds primitives the next consumes. |
| 9 (P6) and 10 (P7) | YES — both depend on P5 only and own different file sets | P6 owns `harness/plugins,scheduler,notebook_chunker,image_extractor,notes,subagents/browser_verifier`; P7 owns `ide/vscode-extension/`, `ide/jetbrains-plugin/`. No overlap. |

---

## Merge order (the only thing the orchestrator (you) needs to enforce)

1. `feature/adr-0049-caching-and-format` → main
2. `feature/adr-0048-two-agent-extraction` → main (rebases onto 0049)
3. `feature/adr-0050-big-repo-recovery` → main (rebases onto 0048)
4. `feature/adr-0051-p1-harness-loop` → main
5. `feature/adr-0051-p2-subagents` → main (rebases onto P1)
6. `feature/adr-0051-p3-skills-memory` → main (rebases onto P2)
7. `feature/adr-0051-p4-hooks-streaming` → main (rebases onto P3)
8. `feature/adr-0052-p5-slash-mcp-workspace` → main (rebases onto P4)
9. `feature/adr-0052-p6-marketplace-and-ecosystem` AND
   `feature/adr-0052-p7-ide-integration`
   → main (in parallel; rebase onto P5)

After step 9 has been green for two weeks:
- Flip `BRAIN_USE_HARNESS=true` as default
- Delete `src/companybrain/pipeline/_orchestrator_legacy.py` and any
  `_legacy` shims
- Update `docs/HARNESS.md` to drop the "(experimental)" framing

---

## Cumulative wins after each session

| After | Cost per run | Wall time | New capabilities |
|---|---|---|---|
| ADR-0049 | $0.30 → $0.05 cold / $0.005 warm | 5min → 3min | Prompt caching live; cross-job dedup; XML inputs |
| ADR-0048 | $0.05 → $0.04 | 3min → 2min | Two-agent extraction (Specialist + Context) |
| ADR-0050 | $0.04 → $0.03 | 2min → 1.5min | Zero silent truncation; works on big repos |
| P1 (harness loop) | $0.03 (no change) | 1.5min | Pipeline is prompt-controlled; new tools easy |
| P2 (sub-agents) | $0.03 → $0.02 | 1.5min → 0.5min | Per-file parallel extraction; isolated context |
| P3 (skills) | $0.02 (no change) | 0.5min | New framework = drop one SKILL.md |
| P4 (hooks/streaming) | $0.02 (no change) | 0.5min | Live UI; per-org hooks; permissions |
| P5 (MCP/slash/headless) | $0.02 (no change) | 0.5min | IDE-callable; CI-callable; SDK; multi-pane rooms |
| P6 (marketplace etc.) | $0.02 (no change) | 0.5min | Plugins; scheduling; notebooks; diagrams; notes |
| P7 (VS Code extn) | $0.02 (no change) | 0.5min | Right-click → ask brain; sidebar; hover tooltips |

End state: **a Claude-Code-equivalent harness for codebase extraction**,
~15× cheaper and ~10× faster than where we started, with framework
extension via skills, IDE integration via MCP, and full ecosystem
(marketplace, scheduling, notes, pinning).
