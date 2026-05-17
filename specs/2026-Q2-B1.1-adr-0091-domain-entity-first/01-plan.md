# Plan — B1.1: ADR-0091 Domain-Entity-First Architecture

**Type**: Writing only. No code changes.

## Files touched

| File | Action | Justification |
|------|--------|---------------|
| docs/adrs/ADR-0091-domain-entity-first-architecture.md | NEW | The ADR itself |
| specs/2026-Q2-B1.1-adr-0091-domain-entity-first/ | NEW | Spec folder |
| docs/SHIP-LOG.md | NEW | Orchestration tracking |
| company-brain-ai/.gitignore | NEW | Fix .venv tracking to prevent disk bloat |

## No code changes. No rollback needed.

## Architecture decisions made in this ADR

1. Domain entities are first-class; source artifacts are evidence
2. Four resolution tiers in priority order (explicit-link > name-match > semantic-embed > human)
3. Auto-resolve threshold: cross_source_confidence ≥ 0.80
4. Domain URN format: `domain://<slug>@<workspace-id>`
5. RESOLVES_TO edge carries: confidence, method, resolved_at, resolver_version
6. Implementation delegated to B1.3 (ADR-0093)
