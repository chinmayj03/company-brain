"""
Unit tests for hash-chained audit log — ADR-0064 M3.

Coverage:
  - append() increments seq, computes hash chain
  - verify_chain() passes on clean log
  - verify_chain() fails on tampered entry (field mutation)
  - read_range() returns correct slice
  - AuditWriter convenience wrapper
  - AuditQuery filters (op, actor, workspace, pagination)
  - Chain integrity with many entries
"""
from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from companybrain.audit.audit_log import AuditLog, AuditEntry, GENESIS_HASH
from companybrain.audit.audit_writer import AuditWriter
from companybrain.audit.audit_query import AuditQuery


# ── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_log(tmp_path: Path) -> AuditLog:
    return AuditLog(path=tmp_path / "audit.jsonl")


@pytest.fixture
def writer(tmp_log: AuditLog) -> AuditWriter:
    return AuditWriter(tmp_log, default_actor="test_actor", default_workspace="test_ws")


# ── AuditLog basics ───────────────────────────────────────────────────────────

class TestAuditLogAppend:
    def test_first_entry_has_seq_1(self, tmp_log: AuditLog):
        e = tmp_log.append(actor="pipeline", workspace="acme", op="entity_create")
        assert e.seq == 1

    def test_first_entry_prev_hash_is_genesis(self, tmp_log: AuditLog):
        e = tmp_log.append(actor="pipeline", workspace="acme", op="entity_create")
        assert e.prev_hash == GENESIS_HASH

    def test_second_entry_prev_hash_links_to_first(self, tmp_log: AuditLog):
        e1 = tmp_log.append(actor="a", workspace="w", op="entity_create")
        e2 = tmp_log.append(actor="a", workspace="w", op="entity_update")
        assert e2.prev_hash == e1.self_hash

    def test_seq_increments(self, tmp_log: AuditLog):
        for i in range(5):
            e = tmp_log.append(actor="a", workspace="w", op="ingest_chunk")
            assert e.seq == i + 1

    def test_self_hash_is_not_empty(self, tmp_log: AuditLog):
        e = tmp_log.append(actor="a", workspace="w", op="entity_create")
        assert len(e.self_hash) == 64  # sha256 hex

    def test_entry_persisted_to_file(self, tmp_log: AuditLog, tmp_path: Path):
        tmp_log.append(actor="a", workspace="w", op="entity_create")
        lines = (tmp_path / "audit.jsonl").read_text().strip().split("\n")
        assert len(lines) == 1
        data = json.loads(lines[0])
        assert data["seq"] == 1
        assert data["op"] == "entity_create"

    def test_count_reflects_appends(self, tmp_log: AuditLog):
        for _ in range(7):
            tmp_log.append(actor="a", workspace="w", op="ingest_chunk")
        assert tmp_log.count() == 7

    def test_diff_and_target_stored(self, tmp_log: AuditLog):
        e = tmp_log.append(
            actor="svc",
            workspace="acme",
            op="entity_create",
            target_urn="urn:cb:acme:entity:001",
            diff={"before": None, "after": {"name": "Foo"}},
        )
        assert e.target_urn == "urn:cb:acme:entity:001"
        assert e.diff == {"before": None, "after": {"name": "Foo"}}


# ── Chain verification ────────────────────────────────────────────────────────

class TestChainVerification:
    def test_empty_log_verifies(self, tmp_log: AuditLog):
        result = tmp_log.verify_chain()
        assert result.is_valid is True
        assert result.entry_count == 0

    def test_clean_10_entry_log_verifies(self, tmp_log: AuditLog):
        for _ in range(10):
            tmp_log.append(actor="pipeline", workspace="acme", op="entity_create")
        result = tmp_log.verify_chain()
        assert result.is_valid is True
        assert result.entry_count == 10

    def test_tamper_self_hash_detected(self, tmp_log: AuditLog, tmp_path: Path):
        """Modify a stored self_hash; verify_chain must catch it."""
        for _ in range(5):
            tmp_log.append(actor="pipeline", workspace="acme", op="entity_create")

        log_path = tmp_path / "audit.jsonl"
        lines = log_path.read_text().strip().split("\n")

        # Tamper seq=3: change op field (but don't update self_hash)
        data = json.loads(lines[2])
        data["op"] = "TAMPERED"
        lines[2] = json.dumps(data)
        log_path.write_text("\n".join(lines) + "\n")

        # Re-open log to read from file
        fresh_log = AuditLog(path=log_path)
        result = fresh_log.verify_chain()
        assert result.is_valid is False
        assert result.first_bad_seq == 3

    def test_tamper_diff_field_detected(self, tmp_log: AuditLog, tmp_path: Path):
        """Modify diff field in seq=5; chain should break at seq=5."""
        for i in range(8):
            tmp_log.append(
                actor="svc",
                workspace="w",
                op="entity_create",
                diff={"after": {"seq": i}},
            )

        log_path = tmp_path / "audit.jsonl"
        lines = log_path.read_text().strip().split("\n")

        data = json.loads(lines[4])   # seq=5 (0-indexed line 4)
        data["diff"]["after"]["injected"] = "malicious"
        lines[4] = json.dumps(data)
        log_path.write_text("\n".join(lines) + "\n")

        fresh_log = AuditLog(path=log_path)
        result = fresh_log.verify_chain()
        assert result.is_valid is False
        assert result.first_bad_seq == 5

    def test_tamper_actor_detected(self, tmp_log: AuditLog, tmp_path: Path):
        for _ in range(3):
            tmp_log.append(actor="honest_actor", workspace="w", op="entity_create")

        log_path = tmp_path / "audit.jsonl"
        lines = log_path.read_text().strip().split("\n")
        data = json.loads(lines[1])   # seq=2
        data["actor"] = "EVIL_ACTOR"
        lines[1] = json.dumps(data)
        log_path.write_text("\n".join(lines) + "\n")

        fresh_log = AuditLog(path=log_path)
        result = fresh_log.verify_chain()
        assert result.is_valid is False
        assert result.first_bad_seq == 2


# ── read_range ────────────────────────────────────────────────────────────────

class TestReadRange:
    def test_read_range_returns_correct_slice(self, tmp_log: AuditLog):
        for _ in range(10):
            tmp_log.append(actor="a", workspace="w", op="ingest_chunk")

        entries = tmp_log.read_range(from_seq=3, to_seq=6)
        assert [e.seq for e in entries] == [3, 4, 5, 6]

    def test_read_range_no_bounds_returns_all(self, tmp_log: AuditLog):
        for _ in range(5):
            tmp_log.append(actor="a", workspace="w", op="ingest_chunk")
        entries = tmp_log.read_range()
        assert len(entries) == 5

    def test_read_range_single_entry(self, tmp_log: AuditLog):
        for _ in range(5):
            tmp_log.append(actor="a", workspace="w", op="ingest_chunk")
        entries = tmp_log.read_range(from_seq=3, to_seq=3)
        assert len(entries) == 1
        assert entries[0].seq == 3


# ── AuditWriter ───────────────────────────────────────────────────────────────

class TestAuditWriter:
    def test_record_appends_entry(self, writer: AuditWriter, tmp_log: AuditLog):
        entry = writer.record("entity_create", target_urn="urn:cb:test")
        assert entry.seq == 1
        assert entry.op == "entity_create"
        assert entry.actor == "test_actor"
        assert entry.workspace == "test_ws"

    def test_record_uses_custom_actor(self, writer: AuditWriter):
        entry = writer.record("query", actor="user@example.com")
        assert entry.actor == "user@example.com"

    def test_record_creates_valid_chain(self, writer: AuditWriter, tmp_log: AuditLog):
        for _ in range(5):
            writer.record("entity_create")
        result = tmp_log.verify_chain()
        assert result.is_valid is True

    def test_record_with_diff(self, writer: AuditWriter):
        diff = {"before": {"name": "Old"}, "after": {"name": "New"}}
        entry = writer.record("entity_update", diff=diff)
        assert entry.diff == diff

    def test_record_increments_seq(self, writer: AuditWriter):
        e1 = writer.record("entity_create")
        e2 = writer.record("entity_update")
        assert e2.seq == e1.seq + 1


# ── AuditQuery ────────────────────────────────────────────────────────────────

class TestAuditQuery:
    def _populate(self, log: AuditLog) -> None:
        ops = ["entity_create", "entity_update", "entity_delete",
               "ingest_chunk", "query"]
        actors = ["pipeline", "pipeline", "user@acme.com", "pipeline", "user@acme.com"]
        workspaces = ["acme", "acme", "acme", "beta", "beta"]
        for op, actor, ws in zip(ops, actors, workspaces):
            log.append(actor=actor, workspace=ws, op=op)

    def test_filter_by_op(self, tmp_log: AuditLog):
        self._populate(tmp_log)
        q = AuditQuery(tmp_log)
        entries = q.query(op="entity_create")
        assert all(e.op == "entity_create" for e in entries)
        assert len(entries) == 1

    def test_filter_by_actor(self, tmp_log: AuditLog):
        self._populate(tmp_log)
        q = AuditQuery(tmp_log)
        entries = q.query(actor="user@acme.com")
        assert all(e.actor == "user@acme.com" for e in entries)
        assert len(entries) == 2

    def test_filter_by_workspace(self, tmp_log: AuditLog):
        self._populate(tmp_log)
        q = AuditQuery(tmp_log)
        entries = q.query(workspace="beta")
        assert all(e.workspace == "beta" for e in entries)
        assert len(entries) == 2

    def test_no_filter_returns_all(self, tmp_log: AuditLog):
        self._populate(tmp_log)
        q = AuditQuery(tmp_log)
        entries = q.query()
        assert len(entries) == 5

    def test_pagination_limit(self, tmp_log: AuditLog):
        for _ in range(20):
            tmp_log.append(actor="a", workspace="w", op="ingest_chunk")
        q = AuditQuery(tmp_log)
        page1 = q.query(limit=5, offset=0)
        page2 = q.query(limit=5, offset=5)
        assert len(page1) == 5
        assert len(page2) == 5
        assert page1[0].seq != page2[0].seq

    def test_combined_filters(self, tmp_log: AuditLog):
        self._populate(tmp_log)
        q = AuditQuery(tmp_log)
        entries = q.query(actor="pipeline", workspace="acme")
        assert all(e.actor == "pipeline" and e.workspace == "acme" for e in entries)
        assert len(entries) == 2

    def test_verify_delegates_to_log(self, tmp_log: AuditLog, writer: AuditWriter):
        writer.record("entity_create")
        q = AuditQuery(tmp_log)
        result = q.verify()
        assert result.is_valid is True

    def test_date_range_filter(self, tmp_log: AuditLog):
        # Append entries with explicit timestamps
        t1 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        t2 = datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)
        t3 = datetime(2026, 12, 1, 12, 0, 0, tzinfo=timezone.utc)
        for ts in [t1, t2, t3]:
            tmp_log.append(actor="a", workspace="w", op="ingest_chunk", timestamp_utc=ts)

        q = AuditQuery(tmp_log)
        entries = q.query(
            from_dt=datetime(2026, 3, 1, tzinfo=timezone.utc),
            to_dt=datetime(2026, 9, 1, tzinfo=timezone.utc),
        )
        assert len(entries) == 1
        assert "2026-06" in entries[0].timestamp_utc
