# Sonnet implementation prompt — one ADR per session

Paste this prompt into a fresh Sonnet session (Claude Code, claude.ai, or
the API) with `<NNNN>` and `<slug>` filled in.

---

## Primary prompt — implement one ADR

```
You are implementing one Architectural Decision Record (ADR) for the
company-brain project. You will work like a senior engineer: read the design
first, implement against acceptance criteria, verify with the documented
commands, then open a PR.

# Project context
Repo: /Users/chinmayjadhav/Documents/Claude/Projects/company-brain
Stack: Java 21 / Spring Boot backend, Python 3.11 / FastAPI AI service,
React/Vite frontend, Bun/TypeScript apps/api + extractor-worker.
Stores: Postgres (semantic graph + RLS), Neo4j 5.18 (structural graph),
Qdrant 1.9 (vectors), Redis (jobs + cache), LocalStack (SQS), Ollama.

# Read these BEFORE writing any code (in order, do not skim):
1. docs/adrs/STAGE-1-IMPLEMENTATION-PLAN.md
2. docs/CURRENT-STATE-and-breakages.md
3. docs/MIGRATION-mono-to-multirepo-to-company.md
4. docs/adrs/ADR-<NNNN>-<slug>.md   ← the one you are implementing

# Your task
Implement ADR-<NNNN>: <one-line title>.

# Workflow
1. Run the pre-flight checklist from STAGE-1-IMPLEMENTATION-PLAN.md
   §"Pre-flight checklist". If anything fails, STOP and report. Do not start
   the ADR until pre-flight is green.
2. Confirm every ADR listed under "Depends on" in this ADR's frontmatter is
   already merged. If not, STOP and report.
3. Create a fresh branch: `git switch -c stage1-adr-<NNNN>-<slug>`.
4. Implement everything in the ADR's "Implementation" section:
   - Create every file under "Files to create" with the documented contents.
     The code skeletons are contracts — function names, signatures, and
     module paths must match. Internals are yours to design.
   - Edit every file under "Files to edit" exactly as specified.
   - Run DB migrations on a copy of dev first; only apply to the live dev DB
     after the dry run is clean.
5. Write the tests under "Test plan" and run them. Every test must pass.
6. Run every command under "Verification commands" and capture the output.
7. Walk the "Acceptance criteria" checklist. Every box must tick. If any does
   not, do NOT mark the ADR complete — fix it or open a follow-up issue.
8. Open a PR titled "ADR-<NNNN>: <title>". Body must contain:
   - The acceptance-criteria checklist with every box checked.
   - The verification-command outputs (or links).
   - Any deviation from the ADR, with one-paragraph justification.

# Hard constraints
- DO NOT do work outside the "Implementation" and "Test plan" sections.
  Items in "Out of scope" are forbidden — they belong to follow-up ADRs.
- DO NOT break existing tests. Run `pytest company-brain-ai/tests/` and
  `mvn -f company-brain-backend test` before opening the PR.
- DO NOT modify any other ADR file.
- DO NOT skip pre-flight.
- If you discover the ADR is wrong (a hidden dependency, a design flaw),
  STOP and report. The fix is a new ADR, not a quiet workaround.

# When you're done, reply with:
- The branch name.
- The list of files created and edited (paths only).
- The acceptance-criteria checklist with every box ticked OR marked
  "BLOCKED: <reason>".
- The verification-command outputs.
- Confirmation that the full test suite still passes.

If you got stuck partway, reply with exactly what you completed and what
blocked you. Do not invent progress.

# Now begin
Implement ADR-<NNNN>.
```

---

## Slug map for the prompt

| ADR  | `<slug>` value                                 | One-line title |
|------|-----------------------------------------------|----------------|
| 0011 | structural-first-extraction                    | Structural-first extraction ordering |
| 0012 | brain-store-json-sot                           | BrainStore + .brain/ JSON source-of-truth |
| 0013 | canonical-urn-identity                         | Canonical URN identity for entities across all stores |
| 0014 | persistent-l2-context                          | Persistent L2 shared context across pipeline runs |
| 0015 | qdrant-hybrid-retriever                        | Qdrant hybrid retriever (BM25S + voyage-code-3 + RRF) |
| 0016 | repo-scoped-cli                                | Repo-scoped extraction trigger and brain CLI |
| 0017 | first-class-assumption-business-context        | Promote assumption and business_context to first-class graph nodes |
| 0018 | smart-zone-assembler                           | Smart-zone context assembler (T0 / T1 / T2 token budgeting) |
| 0019 | mcp-stdio-server                               | MCP stdio server entry point for Claude Code |

---

## Variant — verification-only re-pass

When an ADR's branch already exists and you want a second agent to QA it
without making code changes:

```
You are verifying a completed ADR for the company-brain project.
Branch: stage1-adr-<NNNN>-<slug>
ADR:    docs/adrs/ADR-<NNNN>-<slug>.md

Workflow:
1. `git switch stage1-adr-<NNNN>-<slug>`.
2. Walk every item in the ADR's "Acceptance criteria" checklist.
3. Run every command in the ADR's "Verification commands" section.
4. Run the full Python test suite (pytest company-brain-ai/tests/) and the
   full Java test suite (mvn -f company-brain-backend test).
5. Reply with PASS / FAIL per acceptance-criterion item, the verification
   command outputs, and the test-suite summaries.

Do NOT modify any code. Do NOT mark items PASS unless you verified them
end-to-end with the documented commands.
```

---

## How to run this in practice

- **One Sonnet session per ADR.** Don't try to chain all 9 ADRs in one
  context. Start a fresh session per ADR; the plan's dependency graph
  guarantees nothing is left dangling between sessions.
- **Use git worktrees** so a half-finished ADR doesn't contaminate the
  working tree:
  ```
  git worktree add ../cb-adr-0011 stage1-adr-0011-structural-first-extraction
  cd ../cb-adr-0011
  # Run Sonnet here.
  ```
- **Claude Code recommended invocation:**
  ```
  cd /Users/chinmayjadhav/Documents/Claude/Projects/company-brain
  claude --model sonnet
  # Then paste the prompt with <NNNN> and <slug> filled in.
  ```
- **Order of execution.** Follow the schedule in
  `STAGE-1-IMPLEMENTATION-PLAN.md` §"Ordering — what to ship in which week".
  Do not start an ADR whose dependencies haven't shipped.
- **After each ADR ships,** rebase the next ADR's branch on `main` so it
  picks up the new code.
