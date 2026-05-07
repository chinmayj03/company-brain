# Company Brain — Project Context

## The Problem

Software teams lose critical knowledge constantly, and this happens in three distinct ways:

**1. Tacit knowledge evaporation during development**
The "why" behind code decisions — why a function was implemented a certain way, what alternatives were rejected, what edge cases were considered — is never written down. It lives in the developer's head and disappears the moment they move on.

**2. Knowledge loss when employees leave**
When an engineer leaves a company, they take years of contextual understanding with them. Their understanding of business logic, service interdependencies, and historical decisions is gone, and the team has no way to recover it.

**3. Slow onboarding for new engineers**
New engineers take 2+ months (often longer) to become productive on complex codebases. They spend this time interrupting senior engineers, making wrong assumptions, and shipping bugs because they don't understand the consequences of changes.

---

## The Core Insight

The real problem is not that **code is hard to read**. It's that **knowledge leaves with people**.

A code explainer addresses the symptom. The root problem is the absence of a living, queryable institutional memory — one that captures not just what code does, but why it exists, who owns it, and what breaks when it changes.

---

## The Blast Radius Problem (Critical Technical Constraint)

The sharpest version of the problem: when an engineer changes a database column or an API field, the blast radius can cascade across multiple microservices, frontends, pipelines, and third-party integrations — none of which is visible from inside a single repo.

This context lives in:
- Runtime behavior (distributed tracing logs)
- API contracts (OpenAPI specs, Protobuf, GraphQL schemas)
- CI/CD pipelines (which tests break, which services redeploy)
- Infrastructure-as-code (Terraform, Kubernetes, service mesh configs)

This information **already exists** — it is just scattered across six different tools that don't talk to each other. The product opportunity is aggregating these signals into a single, queryable dependency intelligence layer.

---

## The Larger Vision — A Brain for Every Company

> *"The biggest blocker to AI automation of companies is no longer the models, they just got so good so quickly. Now the blocker is the domain knowledge. Every company has critical know-how scattered everywhere. Some of it lives in people's heads. Some of it is buried in old email accounts, Slack threads, support tickets, and databases. The company works because humans vaguely remember where that knowledge is and how to apply it. But AI agents can't operate like that."*

Every company in the world needs a **Company Brain** — a living map of how it actually operates: how refunds get handled, how pricing exceptions are decided, how engineers respond to incidents. Not a search engine. Not a chatbot over documents. A structured, always-current, executable skills file that AI agents can actually use to do work safely and consistently.

This becomes the missing primitive between raw company data and reliable AI automation:

```
Raw company data  →  Company Brain  →  AI agents that can actually do the work
```

**We are building towards that.** Starting here, with engineering knowledge:
- The dependency graph is the first domain: *what code does, why it exists, what breaks when it changes*
- Business context annotations are the second: *the know-how that lives in people's heads*
- These become the prototype of what every function inside a company will eventually need

The code graph is the beachhead. The vision is the universal company memory layer.

---

## Product Vision (Current Phase)

### What We're Building

A **Dependency Intelligence Platform** — a system that:

1. Aggregates metadata about code from across a company's toolchain (git, CI/CD, observability, API contracts, IaC)
2. Builds and maintains an always-current dependency graph across service boundaries
3. Answers the question developers actually need answered: *"What is the true blast radius of this change?"*
4. Surfaces this intelligence in context, inside the developer's existing workflow

This is not a documentation tool. It is not a code explainer. It is institutional memory made queryable — in real time, at the point of need.

---

## Product Strategy

### Go-to-Market: Extension First, Platform Second

**Phase 1 — VS Code Extension (wedge)**
- Free, self-install, zero procurement friction
- Works immediately on public GitHub repos
- When a developer encounters an unfamiliar function, class, or service call, it surfaces everything knowable: git history, PR descriptions, linked tickets, README context, public API contracts
- Goal: 10,000+ engineers using it without a single enterprise sales call

**Phase 2 — Platform Tier (upgrade path)**
- When a CTO sees adoption and asks "can we connect our private services too?" — that is when the platform offer is made
- The extension is the wedge; the platform is earned trust

**Phase 3 — Self-Hosted Agent (enterprise)**
- Companies run a lightweight metadata agent in their own infrastructure
- The agent extracts structured metadata (not source code) and indexes it
- Only metadata is sent to the platform — code never leaves their environment
- This is the same trust model used by Snyk, Datadog, and GitHub's secret scanning

### Deployment Order
1. VS Code extension
2. Web dashboard (for managers: onboarding progress, service maps, knowledge gaps)
3. Self-hosted agent (for enterprises blocking on security)

Demand pulls the roadmap — do not build all three at once.

---

## What We Are Not Building (and Why)

| Idea | Why We Moved Past It |
|---|---|
| Voice walkthrough of code | Voice is linear; code comprehension is non-linear. Engineers would find it frustrating within a week. |
| General code explainer | This only describes *what* code does, not *why* it was written or *what breaks* if changed. AI inference cannot reconstruct lost business context. |
| Full platform from day one | Requires enterprise sales, security reviews, network effects, and 2+ years before a single paying customer. |
| Code generation (cursor competitor) | Market is saturated. Cannot enter without a differentiated wedge. |

---

## Key Differentiators

- **Not storing code — storing metadata about code.** Far less sensitive, easier to get enterprise buy-in, more useful for dependency mapping than raw source.
- **Cross-boundary intelligence.** Unlike extensions that operate within a single repo, this platform maps dependencies across service boundaries — the gap no existing tool has solved cleanly.
- **Always-current, not manually maintained.** Unlike Backstage/Confluence-style documentation, the graph is built from live signals (traces, contracts, CI), not from engineers writing docs.
- **Context at the point of need.** Intelligence surfaces inside the editor, not in a separate documentation portal that no one opens.

---

## Competitive Landscape

| Tool | What it does | Gap |
|---|---|---|
| GitHub Copilot / Cursor | Code generation and completion | No cross-service dependency awareness |
| Sourcegraph | Code search across repos | No dependency intelligence or institutional memory |
| Backstage (Spotify) | Service catalog, developer portal | Manual maintenance, no real-time blast radius analysis |
| Datadog APM / Jaeger | Distributed tracing, runtime call graphs | Developer tooling surface is weak; not designed for onboarding |
| Confluence / Notion | Documentation | Stale immediately, manually maintained, not code-aware |

**The gap:** an automated, always-current, cross-service dependency intelligence layer with a developer-native surface (editor extension). No one has nailed this.

---

## Data Sources the Platform Will Integrate

| Source | What it provides |
|---|---|
| Git history + PR descriptions | Why code changed, who changed it, linked context |
| OpenAPI / Protobuf / GraphQL schemas | Service interface contracts, field-level dependencies |
| Distributed tracing (OpenTelemetry, Datadog, Jaeger) | Ground truth of runtime service-to-service calls |
| CI/CD pipelines | Which tests break, which services are affected by a change |
| Infrastructure-as-code (Terraform, K8s) | Which services are allowed to communicate |
| Jira / Linear / GitHub Issues | Business context behind tickets that drove code changes |

---

## MVP Definition

**Can we build a version in 4 weeks that a real engineer at a real company would use every day — even if it only works on public GitHub repos and has no cross-service intelligence yet?**

That is v1. The answer should be yes.

v1 scope:
- VS Code extension
- On hover/select of a symbol: surfaces git blame, linked PR description, linked ticket summary, README context
- Works on public repos without any setup
- No voice, no platform, no cross-service graph

Everything else is v2 and beyond.

---

## Strategic Entry into Code Generation

The long-term ambition is to enter the code generation space. The strategy:

1. Get engineers using the product through the knowledge/onboarding wedge
2. Build trust and data advantage through the dependency graph
3. Use the dependency graph as a uniquely powerful context layer for code generation — generating code that *already understands blast radius*, *already knows who owns the affected services*, and *already surfaces risks before the PR is opened*

This is the moat Copilot and Cursor cannot replicate without the same institutional memory layer. The dependency intelligence is not just a feature — it is the defensible foundation for a code generation product that understands consequences, not just syntax.

---

## Open Questions

1. What is the fastest path to a working dependency graph on a real company's codebase?
2. Which integration (git, traces, or API contracts) yields the highest-signal dependency data with the least engineering cost?
3. What is the pricing model — per-seat for the extension, or platform licensing for the metadata layer?
4. Who is the economic buyer — individual engineers (bottom-up, PLG), engineering managers, or platform/DevEx teams (top-down)?
5. What does the onboarding flow look like for an enterprise that wants to connect private services?
