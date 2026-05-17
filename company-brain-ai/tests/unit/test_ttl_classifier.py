"""
Unit tests for TTL classifier and evaluator — ADR-0064 M2.

Coverage:
  - All 6 TTLClass paths
  - Priority ordering (secret > pii_unless_consented > source_type)
  - Time-travel sweep via injectable `now`
  - Redaction correctness
  - Tombstone and hard-delete actions
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from companybrain.privacy.pii_detector import PIIFinding, clear_cache
from companybrain.privacy.ttl_classifier import TTLClass, ttl_classify, expiry_days
from companybrain.privacy.ttl_evaluator import sweep, SweepResult, redact_pii


# ── Fixtures ─────────────────────────────────────────────────────────────────

def make_finding(kind: str, text: str = "test", span=(0, 4)) -> PIIFinding:
    return PIIFinding(kind=kind, text=text, span=span, confidence=0.95, detector="regex")


def make_entity(
    id: str = "urn:cb:test:entity:001",
    ttl_class: str = "transient",
    days_ago: int = 0,
    content: str = "",
    pii_findings: list = None,
) -> dict:
    created = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "id": id,
        "ttl_class": ttl_class,
        "created_at": created.isoformat(),
        "content": content,
        "pii_findings": pii_findings or [],
    }


# ── TTLClass classification ────────────────────────────────────────────────────

class TestTTLClassifier:
    def test_api_key_finding_gives_secret(self):
        findings = [make_finding("api_key")]
        result = ttl_classify("some content", "production", findings)
        assert result == TTLClass.SECRET

    def test_secret_overrides_email(self):
        """Secret has higher priority than email PII."""
        findings = [make_finding("email"), make_finding("api_key")]
        result = ttl_classify("mixed content", "production", findings)
        assert result == TTLClass.SECRET

    def test_email_finding_gives_pii_unless_consented(self):
        findings = [make_finding("email")]
        result = ttl_classify("user email present", "production", findings)
        assert result == TTLClass.PII_UNLESS_CONSENTED

    def test_phone_finding_gives_pii_unless_consented(self):
        findings = [make_finding("phone")]
        result = ttl_classify("phone found", "production", findings)
        assert result == TTLClass.PII_UNLESS_CONSENTED

    def test_ssn_finding_gives_pii_unless_consented(self):
        findings = [make_finding("ssn")]
        result = ttl_classify("ssn found", "production", findings)
        assert result == TTLClass.PII_UNLESS_CONSENTED

    def test_name_finding_gives_pii_unless_consented(self):
        findings = [make_finding("personal_name", "Alice")]
        result = ttl_classify("Alice is a user", "production", findings)
        assert result == TTLClass.PII_UNLESS_CONSENTED

    def test_test_source_gives_transient(self):
        result = ttl_classify("test data", "test", [])
        assert result == TTLClass.TRANSIENT

    def test_fixture_source_gives_transient(self):
        result = ttl_classify("fixture data", "fixture", [])
        assert result == TTLClass.TRANSIENT

    def test_config_source_gives_operational(self):
        result = ttl_classify("DB_URL=...", "config", [])
        assert result == TTLClass.OPERATIONAL

    def test_docs_source_gives_business_indefinite(self):
        result = ttl_classify("# Overview", "documentation", [])
        assert result == TTLClass.BUSINESS_INDEFINITE

    def test_default_gives_permanent(self):
        result = ttl_classify("function foo() {}", "production", [])
        assert result == TTLClass.PERMANENT

    def test_pii_overrides_test_source(self):
        """Email in test code still triggers pii_unless_consented (PII > source_type)."""
        findings = [make_finding("email")]
        result = ttl_classify("user = 'alice@test.com'", "test", findings)
        assert result == TTLClass.PII_UNLESS_CONSENTED


class TestExpiryDays:
    def test_permanent_returns_none(self):
        assert expiry_days(TTLClass.PERMANENT) is None

    def test_secret_returns_zero(self):
        assert expiry_days(TTLClass.SECRET) == 0

    def test_transient_returns_90(self):
        assert expiry_days(TTLClass.TRANSIENT) == 90

    def test_pii_returns_30(self):
        assert expiry_days(TTLClass.PII_UNLESS_CONSENTED) == 30

    def test_operational_returns_365(self):
        assert expiry_days(TTLClass.OPERATIONAL) == 365

    def test_business_indefinite_returns_7_years(self):
        assert expiry_days(TTLClass.BUSINESS_INDEFINITE) == 365 * 7


# ── Redaction ─────────────────────────────────────────────────────────────────

class TestRedactPII:
    def test_redacts_single_span(self):
        text = "Email: alice@example.com here."
        findings = [make_finding("email", "alice@example.com", span=(7, 24))]
        result = redact_pii(text, findings)
        assert "[REDACTED:email]" in result
        assert "alice@example.com" not in result

    def test_redacts_multiple_spans(self):
        text = "John called 415-555-1234."
        # Simulate findings (spans are approximate for this test)
        f1 = PIIFinding("personal_name", "John", (0, 4), 0.7, "dictionary")
        f2 = PIIFinding("phone", "415-555-1234", (12, 24), 0.95, "regex")
        result = redact_pii(text, [f1, f2])
        assert "[REDACTED:personal_name]" in result
        assert "[REDACTED:phone]" in result
        assert "John" not in result
        assert "415-555-1234" not in result

    def test_preserves_surrounding_text(self):
        text = "Hi alice@example.com, thanks!"
        findings = [PIIFinding("email", "alice@example.com", (3, 20), 0.95, "regex")]
        result = redact_pii(text, findings)
        assert result.startswith("Hi ")
        assert result.endswith(", thanks!")

    def test_empty_findings_returns_unchanged(self):
        text = "No PII here."
        result = redact_pii(text, [])
        assert result == text


# ── TTL sweep (time-travel) ───────────────────────────────────────────────────

class TestTTLSweep:
    def test_expired_transient_is_deleted(self):
        """transient entity 91 days old → hard_delete."""
        entity = make_entity(ttl_class="transient", days_ago=91)
        deleted_ids = []
        audit_calls = []

        sweep(
            [entity],
            now=datetime.now(timezone.utc),
            delete_fn=lambda eid: deleted_ids.append(eid),
            audit_fn=lambda op, p: audit_calls.append((op, p)),
        )

        assert entity["id"] in deleted_ids
        assert any(op == "ttl_expire" for op, _ in audit_calls)

    def test_not_yet_expired_transient_skipped(self):
        """transient entity 50 days old → not yet expired."""
        entity = make_entity(ttl_class="transient", days_ago=50)
        deleted_ids = []

        result = sweep([entity], now=datetime.now(timezone.utc),
                       delete_fn=lambda eid: deleted_ids.append(eid))

        assert not deleted_ids
        assert result.skipped == 1

    def test_expired_pii_entity_is_redacted(self):
        """pii_unless_consented entity 31 days old → redact PII spans."""
        content = "Contact alice@example.com for details."
        pii_data = [{"kind": "email", "text": "alice@example.com",
                     "span": [8, 25], "confidence": 0.95, "detector": "regex"}]
        entity = make_entity(
            ttl_class="pii_unless_consented",
            days_ago=31,
            content=content,
            pii_findings=pii_data,
        )
        written_entities = []

        result = sweep(
            [entity],
            now=datetime.now(timezone.utc),
            write_fn=lambda e: written_entities.append(e),
        )

        assert result.redacted == 1
        assert written_entities
        assert "alice@example.com" not in written_entities[0].get("content", "")
        assert "[REDACTED:email]" in written_entities[0].get("content", "")

    def test_expired_operational_is_tombstoned(self):
        """operational entity 366 days old → tombstone."""
        entity = make_entity(ttl_class="operational", days_ago=366)
        written = []

        result = sweep(
            [entity],
            now=datetime.now(timezone.utc),
            write_fn=lambda e: written.append(e),
        )

        assert result.tombstoned == 1
        assert written and written[0].get("tombstoned") is True

    def test_permanent_entity_never_expires(self):
        """permanent entity 10 years old → never acted on."""
        entity = make_entity(ttl_class="permanent", days_ago=3650)
        deleted_ids = []
        written = []

        result = sweep(
            [entity],
            now=datetime.now(timezone.utc),
            delete_fn=lambda eid: deleted_ids.append(eid),
            write_fn=lambda e: written.append(e),
        )

        assert not deleted_ids
        assert not written
        assert result.skipped == 1

    def test_time_travel_mock(self):
        """Time-travel: advance clock to trigger expiry that wouldn't happen today."""
        entity = make_entity(ttl_class="transient", days_ago=10)  # only 10 days old
        deleted_ids = []

        # Advance clock 90 days into the future
        future_now = datetime.now(timezone.utc) + timedelta(days=80)

        result = sweep(
            [entity],
            now=future_now,
            delete_fn=lambda eid: deleted_ids.append(eid),
        )

        assert entity["id"] in deleted_ids, "Entity should be deleted in time-travel future"

    def test_audit_fn_called_for_each_action(self):
        """Audit function is called once per expired entity."""
        entities = [
            make_entity(id="urn:1", ttl_class="transient", days_ago=91),
            make_entity(id="urn:2", ttl_class="transient", days_ago=91),
            make_entity(id="urn:3", ttl_class="permanent", days_ago=3650),
        ]
        audit_calls = []
        sweep(
            entities,
            now=datetime.now(timezone.utc),
            delete_fn=lambda _: None,
            audit_fn=lambda op, p: audit_calls.append((op, p)),
        )
        # 2 transient expired + 1 permanent (no audit for skipped)
        assert len(audit_calls) == 2

    def test_sweep_result_counts(self):
        entities = [
            make_entity(id="urn:1", ttl_class="transient", days_ago=91),
            make_entity(id="urn:2", ttl_class="operational", days_ago=366),
            make_entity(id="urn:3", ttl_class="permanent", days_ago=3650),
        ]
        result = sweep(
            entities,
            now=datetime.now(timezone.utc),
            delete_fn=lambda _: None,
            write_fn=lambda _: None,
        )
        assert result.deleted == 1
        assert result.tombstoned == 1
        assert result.skipped == 1
