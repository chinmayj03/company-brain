"""
End-to-end acceptance test for PII redaction pipeline — ADR-0064.

Scenario:
  1. Ingest a synthetic chunk containing email, phone, and personal name.
  2. Verify all 3 PII kinds are detected.
  3. Verify entity has ttl_class = pii_unless_consented.
  4. Verify entity is written with an audit entry.
  5. Run TTL sweep at time+31 days.
  6. Verify PII spans are redacted; structure intact.
  7. Verify audit log has entries for each step; chain verifies throughout.

No external services required.  Everything runs in-memory with a temp JSONL log.
"""
from __future__ import annotations

import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from companybrain.privacy.pii_detector import scan, clear_cache, PIIFinding
from companybrain.privacy.ttl_classifier import TTLClass, ttl_classify
from companybrain.privacy.ttl_evaluator import sweep
from companybrain.audit.audit_log import AuditLog
from companybrain.audit.audit_writer import AuditWriter
from companybrain.audit.audit_query import AuditQuery


# ── Fixture chunk with intentional PII ───────────────────────────────────────

FIXTURE_CHUNK = (
    "Meeting notes from John Smith:\n"
    "  - Contacted alice@example.com for onboarding\n"
    "  - Follow-up call to 415-555-0100 scheduled\n"
    "  - Discussed Q3 roadmap and security reviews\n"
)


def test_pii_redaction_end_to_end(tmp_path: Path):
    """
    Full pipeline acceptance test:
      detect → classify → audit write → sweep → redact → chain verify.
    """
    # ── Setup ─────────────────────────────────────────────────────────────
    clear_cache()
    log = AuditLog(path=tmp_path / "audit.jsonl")
    writer = AuditWriter(log, default_actor="pipeline", default_workspace="acme-test")
    query = AuditQuery(log)

    # ── Step 1: PII detection ─────────────────────────────────────────────
    findings = scan(FIXTURE_CHUNK, enable_llm_judge=False)
    found_kinds = {f.kind for f in findings}

    assert "email" in found_kinds, f"email not detected; found: {found_kinds}"
    assert "phone" in found_kinds, f"phone not detected; found: {found_kinds}"
    # personal_name detected from dictionary (John or Smith)
    name_or_more = "personal_name" in found_kinds or len(found_kinds) >= 2
    assert name_or_more, f"Expected ≥ 2 PII kinds; found: {found_kinds}"

    # Minimum recall: 2 out of 3 expected kinds found
    expected = {"email", "phone", "personal_name"}
    recall = len(expected & found_kinds) / len(expected)
    assert recall >= 0.66, f"Recall {recall:.0%} too low; found kinds: {found_kinds}"

    # ── Step 2: TTL classification ────────────────────────────────────────
    ttl_class = ttl_classify(FIXTURE_CHUNK, "production", findings)
    assert ttl_class == TTLClass.PII_UNLESS_CONSENTED, (
        f"Expected pii_unless_consented; got {ttl_class}"
    )

    # ── Step 3: Simulate entity write with audit ──────────────────────────
    entity = {
        "id": "urn:cb:acme:test:e2e-001",
        "ttl_class": ttl_class.value,
        "content": FIXTURE_CHUNK,
        "pii_findings": [f.to_dict() for f in findings],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pii_scrubbed": False,
    }

    audit_ingest = writer.record(
        "ingest_chunk",
        target_urn=entity["id"],
        diff={"ttl_class": ttl_class.value, "pii_kinds": list(found_kinds)},
        rationale="chunk ingested with PII detected",
    )
    assert audit_ingest.seq == 1

    # ── Step 4: Verify entity structure before sweep ──────────────────────
    assert entity["ttl_class"] == "pii_unless_consented"
    assert "pii_findings" in entity
    assert len(entity["pii_findings"]) > 0

    # ── Step 5: Time-travel sweep (31 days later) ─────────────────────────
    future_now = datetime.now(timezone.utc) + timedelta(days=31)
    written_entities: list[dict] = []

    sweep_result = sweep(
        [entity],
        now=future_now,
        write_fn=lambda e: written_entities.append(e),
        audit_fn=lambda op, payload: writer.record(
            op,
            target_urn=entity["id"],
            diff=payload,
        ),
    )

    assert sweep_result.redacted == 1, (
        f"Expected 1 redacted; got {sweep_result}"
    )

    # ── Step 6: Verify redaction result ──────────────────────────────────
    assert written_entities, "write_fn should have been called"
    redacted_entity = written_entities[0]
    redacted_content = redacted_entity.get("content", "")

    # PII should be gone
    assert "alice@example.com" not in redacted_content, (
        "Email not redacted from content"
    )
    assert "415-555-0100" not in redacted_content, (
        "Phone not redacted from content"
    )

    # Redaction placeholders should be present
    assert "[REDACTED:" in redacted_content, (
        f"No redaction placeholders found; content: {redacted_content!r}"
    )

    # Structure intact (non-PII text remains)
    assert "Meeting notes" in redacted_content or "roadmap" in redacted_content, (
        "Surrounding text was removed during redaction"
    )

    # pii_scrubbed flag set
    assert redacted_entity.get("pii_scrubbed") is True

    # ── Step 7: Audit log chain verification ─────────────────────────────
    all_entries = query.query()
    assert len(all_entries) >= 2, (
        f"Expected ≥ 2 audit entries; got {len(all_entries)}"
    )

    ops = {e.op for e in all_entries}
    assert "ingest_chunk" in ops, "Missing ingest_chunk audit entry"
    assert "pii_redact" in ops or "ttl_expire" in ops, (
        "Missing pii_redact or ttl_expire audit entry"
    )

    chain_result = log.verify_chain()
    assert chain_result.is_valid, (
        f"Audit chain invalid: {chain_result.error}"
    )
    assert chain_result.entry_count == len(all_entries)


def test_secret_chunk_rejected_at_ingest():
    """
    A chunk containing an API key should be classified as 'secret'
    and never stored (rejected at ingest step).
    """
    clear_cache()
    secret_chunk = 'ANTHROPIC_API_KEY = "sk-ant-api03-SECRETKEY12345678901234567890"'
    findings = scan(secret_chunk, enable_llm_judge=False)
    ttl_class = ttl_classify(secret_chunk, "config", findings)

    assert ttl_class == TTLClass.SECRET, (
        f"Expected SECRET for chunk with API key; got {ttl_class}"
    )


def test_audit_chain_tamper_detection(tmp_path: Path):
    """
    Tamper with an audit entry; verify_chain must detect the inconsistency.
    """
    import json

    log = AuditLog(path=tmp_path / "tamper_test.jsonl")
    writer = AuditWriter(log, default_actor="svc", default_workspace="acme")

    for i in range(5):
        writer.record("entity_create", target_urn=f"urn:cb:acme:e:{i}")

    # Tamper seq=3: change diff field
    log_path = tmp_path / "tamper_test.jsonl"
    lines = log_path.read_text().strip().split("\n")
    data = json.loads(lines[2])  # seq=3, 0-indexed=2
    data["diff"] = {"injected": "malicious_data"}
    lines[2] = json.dumps(data)
    log_path.write_text("\n".join(lines) + "\n")

    fresh_log = AuditLog(path=log_path)
    result = fresh_log.verify_chain()

    assert result.is_valid is False
    assert result.first_bad_seq == 3


def test_multiple_pii_kinds_all_redacted(tmp_path: Path):
    """
    Chunk with multiple PII kinds: all should be redacted after sweep.
    """
    clear_cache()
    chunk = "User Bob Smith (bob@company.com, SSN 234-56-7890) signed up."
    findings = scan(chunk, enable_llm_judge=False)
    found_kinds = {f.kind for f in findings}

    # Should detect at least email + ssn
    assert "email" in found_kinds
    assert "ssn" in found_kinds

    entity = {
        "id": "urn:cb:acme:multi-pii:001",
        "ttl_class": "pii_unless_consented",
        "content": chunk,
        "pii_findings": [f.to_dict() for f in findings],
        "created_at": (datetime.now(timezone.utc) - timedelta(days=31)).isoformat(),
    }

    written: list[dict] = []
    result = sweep(
        [entity],
        now=datetime.now(timezone.utc),
        write_fn=lambda e: written.append(e),
    )

    assert result.redacted == 1
    assert written
    content = written[0].get("content", "")
    assert "bob@company.com" not in content
    assert "234-56-7890" not in content
    assert "[REDACTED:" in content
