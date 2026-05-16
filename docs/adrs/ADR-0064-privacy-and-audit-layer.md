# ADR-0064 — Privacy & Audit Layer (PII detection + typed TTLs + hash-chained audit log)

**Status:** Proposed
**Date:** 2026-05-11
**Inspired by:** ContextDB's `privacy/` module (Apache 2.0; pattern adopted, our own implementation per LEGAL-CONTEXTDB-INTEGRATION.md)
**Sequenced with:** independent of 0055-0063; can ship in parallel. **Required for Product 3 (Compliance) — the highest-margin product in PRODUCT-VISION.**

---

## Context

The brain handles customer code — the most sensitive enterprise asset. Today we have:

- **Zero PII detection.** Hardcoded credentials in source files (API keys, passwords, customer emails in test data) flow straight through extraction → embedding → Qdrant. Once embedded, the secret is in our vector index and contributes to similarity search forever. **Compliance failure waiting to happen.**
- **Zero retention policy.** Extracted entities live forever in Postgres. No "delete after 90 days" rule per data class. EU GDPR right-to-erasure requests can't be honoured.
- **Zero tamper-evident audit log.** We log to stderr / log files. No chain-of-custody for what we extracted, when, or who queried what. **Cannot pass SOC2 Type II audit.**

ContextDB ships these three primitives elegantly: PII runs BEFORE the embedder; TTLs are typed per data class; every write is hash-chained for tamper-evidence. We adopt the pattern (legally clean per LEGAL-CONTEXTDB-INTEGRATION.md) with our own implementation.

**Without this ADR**: no SOC2 / PCI / HIPAA / EU-AI-Act enterprise sale closes. Product 3 (Compliance) never ships. The single highest-margin product in our roadmap is blocked.

---

## Decision

Three coordinated mechanisms, each independently shippable:

### M1 — PII detection BEFORE embedding (and BEFORE writing query_text)

```
Source file → Extractor → ENTITY (with raw query_text, code_snippet)
                              ↓
                         PIIScanner.scan()         ← NEW
                              ↓
                  ┌───── PII detected? ─────┐
                  ▼                          ▼
        scrub fields + flag         pass through unchanged
                  ↓                          ↓
                  └────────── ENTITY ────────┘
                              ↓
                  Embedder (sees only scrubbed text)
                              ↓
                  Qdrant + Postgres (scrubbed + flag stored)
```

**PII categories detected** (regex + Microsoft Presidio if installed):
- API keys (AWS, Anthropic, OpenAI, GitHub, Stripe, generic 32+ char tokens with high entropy)
- Email addresses
- Phone numbers (E.164 + US/EU formats)
- Credit card numbers (Luhn-validated)
- SSN, EIN, NIN, Aadhaar
- IP addresses (configurable; private vs public)
- JWT tokens
- Bearer tokens
- Database connection strings with passwords

**Scrubbing policy** (per category, configurable):
- `mask` — replace with `***[REDACTED:API_KEY]***`
- `hash` — replace with `sha256(value)[:12]` (preserves dedup, removes value)
- `drop` — remove the field entirely
- `flag_only` — keep value, flag for human review

Scrubbed entities carry `pii_scrubbed: bool` and `pii_categories: list[str]` for telemetry + audit.

### M2 — Typed TTLs (data-class-specific retention)

Every entity carries a `retention_class` derived from its source + content:

```python
class RetentionClass(str, Enum):
    PRODUCTION_CODE        = "production_code"        # 7 years
    TEST_FIXTURE           = "test_fixture"           # 90 days
    GENERATED_CODE         = "generated_code"         # 30 days
    EPHEMERAL              = "ephemeral"              # 24 hours (CI artifacts)
    CONFIGURATION          = "configuration"          # 7 years
    DOCUMENTATION          = "documentation"          # 7 years
    TEMPORAL_SNAPSHOT      = "temporal_snapshot"      # 1 year (for time-travel queries)
    CONTAINS_PII_FLAGGED   = "pii_flagged"            # 24 hours hard cap (forces review)
    CUSTOMER_OVERRIDE      = "customer_override"      # never auto-delete (with audit trail)
```

A scheduled job (`pipeline/retention_sweeper.py`, runs daily) deletes entities whose `expires_at` has passed. Right-to-erasure: `DELETE /api/entities?owner=email@example.com` immediately purges + records the deletion in the audit log.

### M3 — Hash-chained audit log

Every brain mutation (extract, write, update, delete, query, share) emits an audit event with:

```python
@dataclass
class AuditEvent:
    event_id: str                        # UUID
    workspace_id: str
    actor: str                           # user_id OR system_component
    action: Literal["extract", "write", "update", "delete", "query", "share", "export"]
    resource_urn: str
    before: Optional[dict]               # compact diff (for updates)
    after: Optional[dict]
    source_ip: Optional[str]
    user_agent: Optional[str]
    timestamp: datetime
    prev_event_hash: str                 # the previous event's hash
    this_event_hash: str                 # sha256(prev_hash || event_canonical_json)
    signature: Optional[str]             # HMAC for additional tamper-evidence (optional, key-managed)
```

The `prev_event_hash` field forms a Merkle-chain. Tampering with any past event changes its hash, which breaks the chain at every subsequent event. Verification: `audit_verify_chain(workspace_id, since_date)` walks forward and returns the first inconsistency.

Storage: dedicated `audit_events` Postgres table. Migration V16. **NEVER UPDATED OR DELETED** — append-only by RLS policy. For 7-year retention compliance, we keep them forever (cheap; ~200 bytes per event).

Auditor portal (defer to Product 3): read-only HTTP endpoint that streams events for a date range with chain verification.

---

## File ownership for THIS PR (parallel-safe with 0055-0063)

```
company-brain-ai/src/companybrain/privacy/                          # NEW DIRECTORY
company-brain-ai/src/companybrain/privacy/__init__.py
company-brain-ai/src/companybrain/privacy/pii_detector.py           # M1 main
company-brain-ai/src/companybrain/privacy/pii_patterns.py           # regex catalog
company-brain-ai/src/companybrain/privacy/scrubber.py               # mask/hash/drop policies
company-brain-ai/src/companybrain/privacy/retention_classes.py      # M2 enum + defaults
company-brain-ai/src/companybrain/privacy/retention_sweeper.py      # M2 scheduled job
company-brain-ai/src/companybrain/audit/                            # NEW DIRECTORY
company-brain-ai/src/companybrain/audit/__init__.py
company-brain-ai/src/companybrain/audit/hash_chain.py               # M3 main
company-brain-ai/src/companybrain/audit/event_writer.py             # M3 writer with append-only RLS
company-brain-ai/src/companybrain/audit/chain_verifier.py           # M3 verification + corruption detection
db/migrations/V16__audit_events_and_retention.sql                    # NEW
tests/unit/test_pii_detector.py                                       # NEW
tests/unit/test_audit_chain.py                                        # NEW
tests/acceptance/test_pii_does_not_leak_to_qdrant.py                  # NEW
tests/acceptance/test_audit_chain_tamper_evident.py                   # NEW
tests/acceptance/test_retention_sweeper.py                            # NEW
```

Append-only edits to:

```
company-brain-ai/src/companybrain/models/entities.py        # add pii_scrubbed, pii_categories, retention_class, expires_at
company-brain-ai/src/companybrain/pipeline/orchestrator.py  # invoke pii_detector after extraction, before storage
company-brain-ai/src/companybrain/retrieval/qdrant_writer.py  # never write entities with pii_scrubbed=False && pii_categories present
company-brain-ai/src/companybrain/store/postgres_store.py    # write audit event on every mutation
company-brain-ai/src/companybrain/api/routes/query.py        # write audit event on every query
company-brain-ai/src/companybrain/api/routes/admin.py        # NEW endpoint: DELETE /entities/by-owner; audit-trail it
pyproject.toml                                                # add presidio-analyzer (optional dep)
```

Does NOT touch any file owned by ADR-0055/56/57/58/59/60/61/62/63.

---

## Acceptance test

```python
async def test_api_key_in_source_file_is_scrubbed_before_embedding(monkeypatch):
    """A source file containing 'sk-ant-api03-XXX' must NEVER reach Qdrant unscrubbed."""
    fixture = make_source_with_pii(content='ANTHROPIC_API_KEY = "sk-ant-api03-AAAA"')
    embedded_texts = []
    monkeypatch.setattr("...embedder.embed", lambda txt: embedded_texts.append(txt))
    await run_pipeline_harness(repo=fixture)
    assert all("sk-ant-api03" not in txt for txt in embedded_texts)


async def test_email_in_test_fixture_classified_as_test_data():
    """An email in tests/ is acceptable for 90-day retention; an email in
    main/ triggers PII flag with 24h hard cap."""
    fixture_test = make_source_at("tests/foo.py", "user_email = 'alice@example.com'")
    fixture_main = make_source_at("src/foo.py", "user_email = 'alice@example.com'")
    test_entity = await extract_one(fixture_test)
    main_entity = await extract_one(fixture_main)
    assert test_entity.retention_class == RetentionClass.TEST_FIXTURE
    assert main_entity.retention_class == RetentionClass.CONTAINS_PII_FLAGGED


async def test_audit_chain_detects_tampering():
    """Mutate an audit row in Postgres; chain verification must catch it."""
    await run_pipeline_harness(repo="fixtures/...")
    await postgres.execute(
        "UPDATE audit_events SET action = 'fake' WHERE event_id = $1",
        sample_event_id,
    )
    result = await audit_verify_chain(workspace_id=ws, since_date=yesterday)
    assert result.is_valid is False
    assert result.first_inconsistency_event_id == sample_event_id


async def test_retention_sweeper_drops_expired_entities():
    """A test_fixture entity created 91 days ago is auto-deleted today."""
    await create_entity_with_creation_date(retention_class="test_fixture", days_ago=91)
    await run_retention_sweeper()
    assert await entity_count(retention_class="test_fixture") == 0
    # And the deletion is audited
    deletions = await audit_query(action="delete", since="-1h")
    assert any(d.resource_urn == "..." for d in deletions)


async def test_right_to_erasure_request_purges_owner_data():
    """DELETE /entities/by-owner=alice@example.com purges all entities containing
    that email AND records the deletion in the audit log."""
    await create_entities_referencing("alice@example.com")
    await api.delete("/entities/by-owner", params={"owner": "alice@example.com"})
    assert await entity_count_referencing("alice@example.com") == 0
    audit = await audit_query(action="delete")
    assert any("alice@example.com" in str(a.before) for a in audit)
```

---

## Effort estimate

4 days, parallelisable to ~2 days with 2 sessions:

| Workstream | Days |
|---|---|
| M1 — PII detector + scrubber + tests | 1.5 |
| M2 — Retention classes + sweeper + V16 migration | 1 |
| M3 — Audit chain + writer + verifier + V16 migration | 1.5 |

---

## Action items

1. [ ] `privacy/pii_patterns.py` — regex catalog for 9 PII categories.
2. [ ] `privacy/pii_detector.py` — scan + classify; optional Presidio integration.
3. [ ] `privacy/scrubber.py` — mask/hash/drop/flag_only policies per category.
4. [ ] `privacy/retention_classes.py` — RetentionClass enum + classification rules.
5. [ ] `privacy/retention_sweeper.py` — scheduled job; deletes expired; audits deletion.
6. [ ] `audit/hash_chain.py` — Merkle-chain event creation.
7. [ ] `audit/event_writer.py` — append-only RLS-protected writes.
8. [ ] `audit/chain_verifier.py` — walk forward; report first inconsistency.
9. [ ] V16 migration: `audit_events` table + `entities.{pii_scrubbed,pii_categories,retention_class,expires_at}` columns.
10. [ ] Wire `pii_detector.scan()` into orchestrator post-extraction.
11. [ ] Wire `audit_writer.write()` into all entity mutation paths.
12. [ ] Wire `audit_writer.write()` into `/query` (read-side audit).
13. [ ] `DELETE /api/entities/by-owner` endpoint for right-to-erasure.
14. [ ] Acceptance: 5 tests above all PASS.
15. [ ] Telemetry: per-run `pii_scrubbed_count`, `pii_categories_distribution`, `retention_sweeper_deleted_count`, `audit_events_written`.
