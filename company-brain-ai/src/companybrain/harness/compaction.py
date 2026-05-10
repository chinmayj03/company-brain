"""Auto-compaction of the parent harness conversation (ADR-0051 P4).

When the running token usage crosses a threshold of the model's context
window, the harness compacts the message history in place. The strategy
preserves the head (system prompt + user task) and the tail (recent tool
results + assistant turns), and replaces the middle with a one-line summary
referring callers to ``HarnessResult.messages`` for the full transcript.

Why preserve head + tail
------------------------

* The **head** carries the canonical pipeline prompt and the user's task —
  the model needs both to keep operating coherently.
* The **tail** carries the most recent decisions: what tools just ran, what
  they returned. That's the working set the next turn reasons over.
* The **middle** is mostly completed sub-agent transcripts — full of the
  per-file extraction noise we already persisted to ``.brain/``. Dropping
  it costs nothing the model needs to keep working.

Why no LLM-based summary
------------------------

We considered calling a cheap model to summarise the middle. Rejected for P4:
adds another failure mode, blocks the loop on an extra round-trip, and the
recipient model rarely needs the dropped content (it ran the tool calls
itself; results are already cached in ``.brain/``). If quality regresses, the
follow-up is to teach sub-agents to return tighter ``final_text``.
"""
from __future__ import annotations

from dataclasses import dataclass

import structlog

from companybrain.llm.base import ChatMessage

log = structlog.get_logger(__name__)


# Nominal context window; provider-agnostic ceiling. Claude has 200K, Llama
# 4 has 128K. We err on the side of the larger window because needs_compaction
# is checked against this value × COMPACT_THRESHOLD; under-counting would
# trigger spurious compactions on shorter-context providers but never miss a
# real one. Override per-provider via Settings if needed.
CONTEXT_LIMIT_TOKENS: int = 200_000

# Compact when running input usage exceeds this fraction of CONTEXT_LIMIT_TOKENS.
# 0.80 leaves 40K headroom (at 200K) for one more multi-tool turn before the
# next compaction. Tighter (0.90) saves a few cents on average runs but
# spikes risk a hard truncation by the provider.
COMPACT_THRESHOLD: float = 0.80

# Minimum messages worth compacting. Below this we'd save nothing and risk
# losing context — return the original list unchanged.
MIN_MESSAGES_TO_COMPACT: int = 12

# How many recent messages to keep in the tail. Sized to cover one
# multi-tool assistant turn (1) + its tool results (~6) + the last
# user/system pair (2). 10 covers the typical hot working set.
TAIL_KEEP: int = 10

# How many head messages to keep verbatim. system + first user message.
HEAD_KEEP: int = 2


@dataclass(frozen=True)
class CompactionDecision:
    """Diagnostic record returned alongside the compacted message list.

    `compacted` is False when no compaction happened (below threshold or too
    few messages); the loop only logs an event in that case.
    """

    compacted: bool
    dropped: int
    kept: int
    usage_total_before: int
    threshold_tokens: int


def needs_compaction(
    messages: list[ChatMessage],
    usage_total: int,
    *,
    context_limit: int = CONTEXT_LIMIT_TOKENS,
    threshold: float = COMPACT_THRESHOLD,
) -> bool:
    """Return True if the running input ``usage_total`` is past the threshold.

    `messages` is required so the loop can also short-circuit the check on
    very short conversations (we don't compact a 4-message run even if
    usage is artificially high — there's nothing to drop).
    """
    if len(messages) < MIN_MESSAGES_TO_COMPACT:
        return False
    return usage_total > int(context_limit * threshold)


def compact(
    messages: list[ChatMessage],
    *,
    head_keep: int = HEAD_KEEP,
    tail_keep: int = TAIL_KEEP,
    usage_total_before: int = 0,
    context_limit: int = CONTEXT_LIMIT_TOKENS,
    threshold: float = COMPACT_THRESHOLD,
) -> tuple[list[ChatMessage], CompactionDecision]:
    """Compact a message list in-place-style: returns a new list + decision.

    No mutation of the input. The caller swaps its ``messages`` reference for
    the returned list.

    Behaviour:
      * Below :data:`MIN_MESSAGES_TO_COMPACT` total messages → unchanged.
      * Otherwise: keep the first ``head_keep`` and the last ``tail_keep``;
        replace the middle with a single ``user`` message describing the
        truncation. Choosing ``user`` (not ``system``) keeps the system
        message exactly the prompt the agent started with — providers cache
        system prefixes and we don't want to bust that cache.
    """
    n = len(messages)
    if n < MIN_MESSAGES_TO_COMPACT:
        return messages, CompactionDecision(
            compacted=False,
            dropped=0,
            kept=n,
            usage_total_before=usage_total_before,
            threshold_tokens=int(context_limit * threshold),
        )

    if head_keep + tail_keep >= n:
        # Pathological config: keeping more than we have. No-op.
        return messages, CompactionDecision(
            compacted=False, dropped=0, kept=n,
            usage_total_before=usage_total_before,
            threshold_tokens=int(context_limit * threshold),
        )

    head = messages[:head_keep]
    tail = messages[-tail_keep:]
    middle_count = n - head_keep - tail_keep

    summary = ChatMessage(
        role="user",
        content=(
            f"<compacted>{middle_count} earlier turns dropped to free context — "
            f"see HarnessResult.messages for the full transcript.</compacted>"
        ),
    )
    new_messages = [*head, summary, *tail]

    decision = CompactionDecision(
        compacted=True,
        dropped=middle_count,
        kept=len(new_messages),
        usage_total_before=usage_total_before,
        threshold_tokens=int(context_limit * threshold),
    )
    log.info(
        "harness.compacted",
        dropped=middle_count,
        kept=len(new_messages),
        usage_before=usage_total_before,
        threshold=decision.threshold_tokens,
    )
    return new_messages, decision


__all__ = [
    "CONTEXT_LIMIT_TOKENS",
    "COMPACT_THRESHOLD",
    "MIN_MESSAGES_TO_COMPACT",
    "TAIL_KEEP",
    "HEAD_KEEP",
    "CompactionDecision",
    "needs_compaction",
    "compact",
]
