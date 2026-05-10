"""Unit tests for harness/compaction.py (ADR-0051 P4)."""
from __future__ import annotations

from companybrain.harness import compaction
from companybrain.llm.base import ChatMessage


def _msgs(n: int) -> list[ChatMessage]:
    """Build n messages alternating user/assistant after the initial system+user."""
    out: list[ChatMessage] = [
        ChatMessage(role="system", content="SYS"),
        ChatMessage(role="user",   content="TASK"),
    ]
    for i in range(n):
        role = "assistant" if i % 2 == 0 else "user"
        out.append(ChatMessage(role=role, content=f"msg-{i}"))
    return out


# ── needs_compaction ───────────────────────────────────────────────────────


def test_needs_compaction_below_threshold_is_false():
    msgs = _msgs(20)
    # Usage well under 80% of 200K → no compaction.
    assert compaction.needs_compaction(msgs, usage_total=10_000) is False


def test_needs_compaction_above_threshold_is_true():
    msgs = _msgs(20)
    # 80% of 200K = 160K; pick something well above.
    assert compaction.needs_compaction(msgs, usage_total=180_000) is True


def test_needs_compaction_short_history_never_compacts():
    """A 4-message run is too short to drop anything from."""
    short = _msgs(2)  # total 4 messages; below MIN_MESSAGES_TO_COMPACT
    assert compaction.needs_compaction(short, usage_total=10_000_000) is False


def test_needs_compaction_respects_custom_threshold():
    """Passing a tighter threshold/limit triggers compaction at lower usage."""
    msgs = _msgs(20)
    # 0.5 × 100k = 50k threshold; 60k > 50k.
    assert compaction.needs_compaction(
        msgs, usage_total=60_000,
        context_limit=100_000, threshold=0.5,
    ) is True


# ── compact() preserves head + tail, drops the middle ──────────────────────


def test_compact_keeps_head_and_tail_replaces_middle():
    msgs = _msgs(20)  # 22 messages total
    out, decision = compaction.compact(msgs, usage_total_before=160_000)

    assert decision.compacted is True
    assert decision.dropped == 22 - compaction.HEAD_KEEP - compaction.TAIL_KEEP
    # Resulting list = head + 1 summary + tail.
    assert len(out) == compaction.HEAD_KEEP + 1 + compaction.TAIL_KEEP

    # Head preserved verbatim.
    assert out[0].role == "system"
    assert out[0].content == "SYS"
    assert out[1].role == "user"
    assert out[1].content == "TASK"

    # Summary message right after head.
    assert out[2].role == "user"
    assert "compacted" in out[2].content
    assert "earlier turns dropped" in out[2].content

    # Tail preserved verbatim — last message is the same as before.
    assert out[-1].content == msgs[-1].content


def test_compact_below_min_returns_input_unchanged():
    msgs = _msgs(2)  # 4 messages → below MIN_MESSAGES_TO_COMPACT
    out, decision = compaction.compact(msgs)
    assert out is msgs
    assert decision.compacted is False
    assert decision.dropped == 0


def test_compact_pathological_keep_config_is_noop():
    """If head_keep + tail_keep ≥ message count, compaction is a no-op."""
    msgs = _msgs(15)
    out, decision = compaction.compact(msgs, head_keep=10, tail_keep=10)
    assert out is msgs
    assert decision.compacted is False


def test_compact_does_not_mutate_input():
    """Compaction returns a new list; callers' original references stay intact."""
    msgs = _msgs(20)
    snapshot = list(msgs)
    out, _ = compaction.compact(msgs)
    assert msgs == snapshot
    assert out is not msgs
