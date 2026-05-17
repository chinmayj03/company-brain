# Product Requirements Document — Company Brain V2

**Version:** 2.0  
**Date:** 2026-05-17  
**Author:** Chinmay Jadhav  
**Status:** Active — current north star

---

## 1. The Pivot

### What we built (V1)
A CLI tool that indexes a single git repo and answers questions about it via a FastAPI service. A frontend demo that shows the indexed data with blast radius, citations, owners, and time travel.

### What we're building (V2)
**Company Brain** — an AI platform that builds and maintains a persistent, multi-source knowledge graph of everything your engineering org knows. It connects to any knowledge source, keeps itself up to date, and answers questions about code, risk, ownership, drift, and change impact from any surface: the web UI, Cursor, Claude, Devin, VS Code, or any MCP-compatible agent.

### The core insight
Engineering knowledge is distributed and decays. Code doesn't self-document. APIs drift from their specs. Runbooks go stale. New engineers spend weeks learning what senior engineers knew implicitly. Every PR review and incident drains institutional memory into Slack threads that no one can find.

Company Brain compresses that knowledge into a queryable, always-fresh graph — so any engineer, agent, or tool can ask "what does this do?", "what breaks if I change this?", "who owns this?", "how did this change over time?" and get a grounded, cited answer in seconds.

---

## 2. Product Vision

> **Company Brain is the institutional memory layer for engineering teams.** It knows what your code does, who owns it, what it depends on, what could break, and how it's changed — across every source of truth in your engineering org. Any agent or engineer can query it in natural language and get answers with citations, confidence levels, and blast-radius analysis.

### V2 north star metrics
- Time-to-first-answer for a new engineer asking "what does X do?" < 10 seconds
- Blast radius accuracy (entities correctly identified vs. manual review) ≥ 85%
- Source coverage: ≥ 3 connected sources per workspace in active demos
- MCP agent adoption: ≥ 1 agent (Cursor, Claude, Devin) connected per paying team

---

## 3. User Personas

### P1 — Senior Engineer / Tech Lead (primary)
**Name:** Priya  
**Context:** 4 years at the company. Knows the codebase well but is constantly interrupted by "can you explain X?" questions from new hires and agents.  
**Jobs to be done:**
- Answer "what does this service do?" without interrupting her own flow
- Review a PR and instantly see blast radius before approving
- Onboard a new Cursor/Devin agent with full codebase context
- Understand who owns a component before a risky refactor

### P2 — New Engineer (high-value secondary)
**Name:** Marcus  
**Context:** 2 weeks into a new job. Has read the README. Lost.  
**Jobs to be done:**
- Understand a codebase without a dedicated 2-hour pairing session
- Ask "what does CompetitivenessPlanRepository do?" and get a real answer, not docs
- Trace a bug across layers without having to read 5,000 lines of SQL

### P3 — AI Agent (emerging primary)
**Context:** Cursor, Devin, Claude Code, GitHub Copilot — any coding agent that can connect to an MCP server.  
**Jobs to be done:**
- Get grounded context before generating code to avoid hallucinating non-existent APIs
- Check blast radius before suggesting a refactor
- Look up owners to suggest the right reviewer
- Stay within the code's established patterns (anti_patterns field from BusinessContext v2)

### P4 — Engineering Manager (strategic)
**Name:** Alex  
**Context:** Manages 3 teams. Wants visibility into code health without reading PRs.  
**Jobs to be done:**
- See bus factor risks (which components have only 1 owner)
- Track drift between architecture docs and actual code
- Understand which components are the riskiest to change before a big release

---

## 4. Features — Current State & Roadmap

### 4.1 Core query engine ✅ Done

| Feature | Status | ADR |
|---|---|---|
| Natural language query → cited answer | Done | ADR-0043 |
| Confidence scoring (high/medium/low) | Done | ADR-0005 |
| Streaming SSE response | Done | ADR-0073 |
| Hybrid vector + graph retrieval (RRF) | Done | ADR-0065 |
| Multi-hop graph traversal | Done | ADR-0061 |
| BusinessContext v2 (7 typed fields) | Done | ADR-0060 |
| Follow-up question suggestions | Done | ADR-0043 |

### 4.2 Knowledge extraction ✅ Done

| Feature | Status | ADR |
|---|---|---|
| Java/Python/TS AST extraction | Done | ADR-0042, 0047 |
| SQL schema + migration extraction | Done | ADR-0058 |
| jOOQ binding extraction | Done | ADR-0058 |
| Cross-file cross-cutting extraction | Done | ADR-0055 |
| Few-shot library (30 examples) | Done (cache fix pending) | ADR-0060 |
| Universal file extraction | Done | ADR-0057 |
| Temporal + domain inference | Done | ADR-0059 |
| Ownership via git blame | Done | ADR-0059 |

### 4.3 Source ingestion 🚧 In progress

| Feature | Status | ADR |
|---|---|---|
| Git repo indexing (CLI) | Done | ADR-0016 |
| Source registry (DB table) | Done (read-only) | ADR-0072 |
| POST /sources (register source) | **Missing** | ADR-0074 |
| Typed connectors (BaseConnector) | **Missing** | ADR-0074 |
| OpenAPI spec connector | **Missing** | ADR-0074 |
| DB migrations connector | Partial (extractor exists) | ADR-0074 |
| Confluence connector | Not started | ADR-0074 |
| GitHub PR history connector | Not started | ADR-0074 |
| Incremental indexing | Not started | ADR-0074 |

### 4.4 Frontend — query surface ✅ Done (pending ADR-0073 fixes)

| Feature | Status | ADR |
|---|---|---|
| Ask view with streaming answer | Done | ADR-0073 |
| Blast radius graph | Done | ADR-0073 |
| Citations panel | Done | ADR-0073 |
| Owners rail (live git blame) | Done | ADR-0073 |
| Time travel (as_of_date query) | Done | ADR-0073 |
| RepoPicker (repo + branch select) | Done | ADR-0073 |
| History view (live conversations) | Done | ADR-0073 |
| Saved answers | Done | ADR-0073 |
| LiveModeChip (Live/Mock toggle) | Done | ADR-0073 |

### 4.5 Frontend — navigation & UX 🚧 In progress

| Feature | Status | ADR |
|---|---|---|
| Nav icons | **Missing** | ADR-0075 |
| Nav hierarchy (primary/workspace) | **Missing** | ADR-0075 |
| Settings view | **Missing** | ADR-0075 |
| Add source modal (3-step) | **Missing** | ADR-0075 |
| Sources empty state onboarding card | **Missing** | ADR-0075 |
| Ask view scope chip | **Missing** | ADR-0075 |
| Source kind icons (not UUID) | **Missing** | ADR-0075 |
| Fix double getMe() in bootstrap | **Missing** | ADR-0075 |

### 4.6 MCP agent integration ✅ Done

| Feature | Status | ADR |
|---|---|---|
| MCP stdio server | Done | ADR-0019 |
| Agents view (live roster) | Done | ADR-0073 |
| MCP endpoint URL copy | Done | ADR-0073 |
| Native agent framework integrations | In progress | ADR-0068 |

### 4.7 Memory & drift ✅ Done (backend)

| Feature | Status | ADR |
|---|---|---|
| Drift detection | Done | ADR-0007, 0082 |
| Experiential memory tier | Done | ADR-0066 |
| Brain evolution background process | Done | ADR-0067 |
| Event stream memory | Done | ADR-0073 |
| Catalog evolution + curation | Done | ADR-0083 |

### 4.8 Document & context ingestion 📋 Planned

| Feature | Status | ADR |
|---|---|---|
| PRD + architecture doc ingestion | Planned | ADR-0070 |
| Notion workspace connector | Planned | ADR-0074 (future) |
| Jira project connector | Planned | ADR-0074 (future) |
| Datadog SLO connector | Planned | ADR-0074 (future) |

### 4.9 Advanced features 📋 Planned

| Feature | Status | ADR |
|---|---|---|
| Persona-aware query templates | Planned | ADR-0079 |
| Estimation + velocity model | Planned | ADR-0080 |
| Compression + domain graph expansion | Planned | ADR-0084 |
| Ecosystem calibration packs | Planned | ADR-0062 |
| Privacy + audit layer | Done (backend) | ADR-0064 |
| Convention inference augmentation | Done | ADR-0063 |

---

## 5. User Journeys

### J1 — First time setup (New Engineer, Day 1)

```
1. Opens Company Brain at localhost:5173
2. Lands on /ask — sees onboarding banner "Your brain has no sources yet"
3. Clicks "Add your first repo"
4. Modal opens: picks "Git Repo"
5. Types repo path, clicks "Add & Index"
6. Watches live progress: "Extracting AST... 142 files... Building graph..."
7. "Done — 1,432 entities indexed"
8. Lands back on /ask with scope chip showing "my-service@main · 1,432 entities"
9. Types "What does PaymentService do?" → gets cited answer in 3s
```

### J2 — Pre-PR blast radius check (Senior Engineer)

```
1. Opens /ask
2. Types "What breaks if I rename customer_id in the comp_providers table?"
3. Gets answer with blast radius graph (12 affected entities, 3 teams, HIGH risk)
4. Clicks entity in graph → sees full BusinessContext in citation
5. Decides to create a migration and opens /history to copy the question for the PR description
```

### J3 — MCP agent query (Cursor coding agent)

```
1. Agent is writing a new endpoint in CompetitivenessController.java
2. Calls brain MCP tool: query("What are the existing patterns in CompetitivenessController?")
3. Gets answer with anti_patterns, performance_class, transaction_mode from BusinessContext v2
4. Agent generates code that follows the established patterns
5. Agent calls query("Who should review changes to CompetitivenessController?") → gets owners list
```

### J4 — Adding an OpenAPI source (Tech Lead)

```
1. Opens /sources
2. Clicks "+ Add source" → picks "OpenAPI Spec"
3. Enters URL: "https://internal.acme.com/api-spec/payments.yaml"
4. Clicks "Add & Index"
5. 30 endpoints extracted, linked to existing Java entities by method + path matching
6. Now "what does POST /competitiveness/plan accept?" returns cited answer with request schema
```

---

## 6. Non-Functional Requirements

### Performance
- Query response (streaming first token): < 2 seconds
- Query response (full answer): < 10 seconds for P90
- Index throughput: ≥ 20 files/minute for git_local connector
- Incremental sync: < 30 seconds for a single changed file

### Cost
- Index cost: < $2 per 1,000 files (with prompt caching)
- Query cost: < $0.03 per query (Sonnet 3.5 with context compression)
- Few-shot library: ≤ 6,000 bytes serialized (ADR-0060 constraint)

### Reliability
- AI service availability: 99% during business hours (single-node local deployment)
- Pipeline retry: any failed file re-indexed up to 3x (ADR-0029)
- Redis job TTL: 2 hours (jobs cleaned up automatically)

### Security
- API tokens for external sources stored as env var names, not values (V1)
- Audit log for all queries (ADR-0064)
- No PII in Qdrant payloads (file paths + entity names only)

---

## 7. Success Metrics by Phase

### Phase 1 — Demo-ready (now)
- [ ] First-run setup to first answered question in < 5 minutes without CLI
- [ ] All 8 UI checks from E2E test suite pass
- [ ] 15/20 benchmark questions pass against NIQ

### Phase 2 — Multi-source
- [ ] OpenAPI connector works end-to-end with real spec
- [ ] Cross-source queries (Java class → OpenAPI endpoint) work
- [ ] 3 source types connectable from UI

### Phase 3 — Team adoption
- [ ] ≥ 1 MCP agent (Cursor/Claude/Devin) connected per team
- [ ] Daily active queries ≥ 10/team/day
- [ ] Bus factor surface used in ≥ 1 PR review per sprint

---

## 8. Out of Scope (V2)

- Multi-user auth (SSO, RBAC) — single-workspace, single-user for now
- Cloud hosting — local deployment only
- Real-time collaborative editing of BusinessContext
- Fine-tuned models — all LLM calls use Anthropic API
- Billing / usage metering

---

## 9. Open Questions

1. **Secret management** — how do we store Confluence/GitHub API tokens securely? Current plan (env var name in config) works for single-user but not for teams. Needs a secrets ADR.
2. **Source dependency ordering** — if a Confluence page references a Java class, can we resolve that URN cross-source? Current answer: no. Needs cross-source URN resolution.
3. **Incremental indexing trigger** — who triggers re-index? Options: git post-commit hook, polling, manual UI button, or webhook from GitHub. V1: manual UI button only.
4. **Offline/airgapped deployments** — some teams can't hit Anthropic API. Ollama support exists but untested. Needs validation ADR.
