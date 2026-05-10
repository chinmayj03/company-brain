# BRAIN.md — repo-specific brain memory

This file is auto-loaded into the company-brain extraction agent's system
prompt on every run. Use it to capture repo-specific gotchas, conventions,
and anti-patterns the agent should know but cannot infer from the code
alone.

The file has two sections. **Edit the curated section.** Leave the
auto-appended section to the pipeline.

---

## Curated notes (human-edited)

<!--
Examples (delete these and add your own):

- The `lob` column was renamed from `lobName` in 2024-Q3. Old code paths
  still reference `lobName`; treat them as the same field.
- The `JsonKeyMapping` class is a constants table — never extract it as
  a code entity. It exists only to namespace string keys.
- All SQL goes through jOOQ DSL chains; ignore raw SQL strings inside
  `*Specification` classes (those are jOOQ DSL, not raw SQL).
- The legacy `/v1/competitors` endpoint forwards to `/v2/competitors`
  internally — extracting v1 will produce a confusing duplicate.
- `CompetitivenessFacade` is a thin pass-through; always extract the
  underlying `CompetitivenessService` instead.
-->

<!-- AUTO-APPENDED — managed by company-brain. Do not edit by hand. -->
