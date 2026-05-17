# Spec — B1.1: ADR-0091 Domain-Entity-First Architecture

**Sub-session ID**: B1.1
**Type**: Writing-only (framing ADR, no code)
**Budget**: $10 / 0.5 weeks
**Branch**: feature/adr-0091-domain-entity-first → release/v2-seed-window
**Depends on**: T1.1 ✅

## Problem

Code-first brain has no concept of domain entities that span multiple sources. As non-code
connectors (Notion, Slack, Salesforce) arrive, "Customer" exists as a Java class, a Notion page,
and a Salesforce Account with no link between them. Cross-source persona answers are impossible
without a domain-entity model.

## Outcome

ADR-0091 written covering: domain entities as primary addressable unit, DomainEntityRef schema,
cross-source resolution policy (4 tiers + thresholds), source hierarchy, domain URN design
(`domain://<slug>@<workspace>`), RESOLVES_TO edge model, consequences for extraction/query/personas,
implementation sequence B1.1→B1.2→B1.3→B1.4.

## Success criteria

- [x] ADR file exists and covers all required sections
- [x] Resolution policy tiers defined (explicit-link > name-match > embed > human)
- [x] Thresholds set (auto ≥0.80, suggest 0.60–0.80, drop <0.60)
- [x] Domain URN format specified
- [x] RESOLVES_TO edge model specified
- [x] Clear non-goals (no multi-workspace, no federation infra)
