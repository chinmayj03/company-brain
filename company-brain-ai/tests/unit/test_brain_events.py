"""
Unit tests for ADR-0090 BrainEvent schema.

Covers:
  - All required fields present and typed correctly
  - Dataclass is frozen (immutable)
  - Valid event_type accepted; invalid rejected
  - to_dict() round-trip
  - Default field generation (id, timestamps)
  - causal_parents and actors accept tuples
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from companybrain.events.models import BrainEvent, ALL_EVENT_TYPES


# ── Factory helpers ────────────────────────────────────────────────────────────

def _make_event(**overrides) -> BrainEvent:
    defaults = dict(
        workspace_id="ws-001",
        repo="acme/payments",
        branch="main",
        event_type="AgentAction",
        payload={"action": "test"},
        occurred_at=datetime(2026, 5, 17, 12, 0, 0, tzinfo=timezone.utc),
        recorded_at=datetime(2026, 5, 17, 12, 0, 1, tzinfo=timezone.utc),
        causal_parents=(),
        actors=("tester",),
        urn_affected="urn:cb:acme:component:payments:PaymentService",
    )
    defaults.update(overrides)
    return BrainEvent(**defaults)


# ── Schema completeness ────────────────────────────────────────────────────────

def test_brain_event_all_fields_present():
    e = _make_event()
    assert e.id
    assert e.workspace_id == "ws-001"
    assert e.repo == "acme/payments"
    assert e.branch == "main"
    assert e.event_type == "AgentAction"
    assert e.payload == {"action": "test"}
    assert isinstance(e.occurred_at, datetime)
    assert isinstance(e.recorded_at, datetime)
    assert isinstance(e.causal_parents, tuple)
    assert isinstance(e.actors, tuple)
    assert e.urn_affected == "urn:cb:acme:component:payments:PaymentService"


def test_brain_event_is_frozen():
    """BrainEvent must be immutable — mutations must raise FrozenInstanceError."""
    e = _make_event()
    with pytest.raises(Exception):
        e.event_type = "GitCommit"  # type: ignore[misc]


def test_brain_event_id_auto_generated():
    e1 = _make_event()
    e2 = _make_event()
    assert e1.id != e2.id  # UUIDs are distinct
    assert len(e1.id) == 36  # UUID format


def test_brain_event_timestamps_default_to_now():
    before = datetime.now(timezone.utc)
    e = BrainEvent(workspace_id="w", event_type="QueryAsked")
    after = datetime.now(timezone.utc)
    assert before <= e.occurred_at <= after
    assert before <= e.recorded_at <= after


# ── Event type validation ──────────────────────────────────────────────────────

@pytest.mark.parametrize("event_type", ALL_EVENT_TYPES)
def test_all_valid_event_types_accepted(event_type):
    e = _make_event(event_type=event_type)
    assert e.event_type == event_type


def test_invalid_event_type_raises():
    with pytest.raises(ValueError, match="Unknown event_type"):
        _make_event(event_type="NotAnEventType")


def test_all_event_types_count():
    """Regression: ensure we have exactly 20 event types."""
    assert len(ALL_EVENT_TYPES) == 20


# ── Specific event types ───────────────────────────────────────────────────────

@pytest.mark.parametrize("event_type", [
    "GitCommit", "PROpened", "PRMerged", "PRClosed",
    "BranchCreated", "BranchDeleted",
])
def test_code_lifecycle_event_types(event_type):
    e = _make_event(event_type=event_type)
    assert e.event_type == event_type


@pytest.mark.parametrize("event_type", [
    "Deploy", "Rollback", "ConfigChange", "SchemaMigration",
])
def test_deployment_lifecycle_event_types(event_type):
    e = _make_event(event_type=event_type)
    assert e.event_type == event_type


@pytest.mark.parametrize("event_type", [
    "IncidentDeclared", "IncidentMitigated", "IncidentResolved", "PostmortemPublished",
])
def test_incident_lifecycle_event_types(event_type):
    e = _make_event(event_type=event_type)
    assert e.event_type == event_type


@pytest.mark.parametrize("event_type", [
    "HumanFactWritten", "AgentAction", "VerifierCorrection",
    "QueryAsked", "FeedbackGiven",
])
def test_human_agent_event_types(event_type):
    e = _make_event(event_type=event_type)
    assert e.event_type == event_type


def test_external_event_type():
    e = _make_event(event_type="ExternalDocChange")
    assert e.event_type == "ExternalDocChange"


# ── Optional urn_affected ─────────────────────────────────────────────────────

def test_urn_affected_optional():
    e = _make_event(urn_affected=None)
    assert e.urn_affected is None


def test_urn_affected_set():
    urn = "urn:cb:tenant:column:plan_info.lob"
    e = _make_event(urn_affected=urn)
    assert e.urn_affected == urn


# ── Causal parents and actors ─────────────────────────────────────────────────

def test_causal_parents_tuple():
    parent_id = "550e8400-e29b-41d4-a716-446655440000"
    e = _make_event(causal_parents=(parent_id,))
    assert parent_id in e.causal_parents


def test_causal_parents_empty_default():
    e = BrainEvent(workspace_id="w", event_type="QueryAsked")
    assert e.causal_parents == ()


def test_actors_tuple():
    e = _make_event(actors=("alice", "bot/verifier"))
    assert "alice" in e.actors
    assert "bot/verifier" in e.actors


# ── to_dict() ─────────────────────────────────────────────────────────────────

def test_to_dict_keys():
    e = _make_event()
    d = e.to_dict()
    expected_keys = {
        "id", "workspace_id", "repo", "branch", "event_type",
        "payload", "occurred_at", "recorded_at",
        "causal_parents", "actors", "urn_affected",
    }
    assert expected_keys == set(d.keys())


def test_to_dict_timestamps_are_strings():
    e = _make_event()
    d = e.to_dict()
    assert isinstance(d["occurred_at"], str)
    assert isinstance(d["recorded_at"], str)
    # Should be ISO format
    datetime.fromisoformat(d["occurred_at"])
    datetime.fromisoformat(d["recorded_at"])


def test_to_dict_lists_not_tuples():
    e = _make_event(causal_parents=("abc",), actors=("x",))
    d = e.to_dict()
    assert isinstance(d["causal_parents"], list)
    assert isinstance(d["actors"], list)


def test_to_dict_payload_preserved():
    payload = {"commit_sha": "abc123", "files_changed": 3}
    e = _make_event(payload=payload)
    assert e.to_dict()["payload"] == payload


# ── Branch (empty string for cross-branch events) ─────────────────────────────

def test_branch_empty_string_allowed():
    e = _make_event(branch="")
    assert e.branch == ""


def test_branch_value_set():
    e = _make_event(branch="feature/payments-v2")
    assert e.branch == "feature/payments-v2"
