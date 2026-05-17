"""
A1.3 — PromptCacheWrapper

Adds Anthropic cache_control breakpoints to messages so repeated calls
against the same codebase context use Anthropic's Prompt Caching API,
paying only the cache-read rate (~10× cheaper) instead of full input tokens.

The Anthropic provider already adds a breakpoint on the system prompt
(see AnthropicProvider.chat()).  This wrapper adds a SECOND breakpoint on
the first large user-message context block (>500 estimated tokens) so that
assembled codebase context is also eligible for server-side caching.

Non-Anthropic providers are unaffected — the wrapper returns messages
unchanged when provider_name != "anthropic".

Anthropic cache_control rules:
  - Max 4 cache breakpoints per request.
  - Minimum cacheable prefix: 1 024 tokens (Haiku) / 2 048 tokens (Sonnet/Opus).
  - Cache lifetime: 5 minutes (ephemeral).
  - Everything UP TO the breakpoint is cached; the breakpoint is on the
    last content block of the message that should be included in the prefix.
"""
from __future__ import annotations

import copy
from typing import Any

# Approximate chars-per-token for estimating whether a block is large enough
# to be worth a cache breakpoint (Anthropic min: 1 024 / 2 048 tokens).
# Using 4 chars/token — conservative for code-heavy prompts.
_CHARS_PER_TOKEN: int = 4
_MIN_CACHE_CHARS: int = 500 * _CHARS_PER_TOKEN   # ~500 tokens → 2 000 chars


class PromptCacheWrapper:
    """Adds Anthropic cache_control breakpoints to the messages list.

    Usage::

        wrapper = PromptCacheWrapper()
        messages = wrapper.add_cache_breakpoints("anthropic", messages)
        # pass modified messages to anthropic client

    The method is idempotent — running it twice on the same list does not
    add duplicate breakpoints.
    """

    def add_cache_breakpoints(
        self,
        provider_name: str,
        messages: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Return a (deep-copied) messages list with cache_control added.

        For non-Anthropic providers this is a no-op and returns the original
        list object unchanged (no copy is made).

        Args:
            provider_name: Lower-case provider identifier, e.g. "anthropic".
            messages: List of message dicts in Anthropic's API format
                      [{"role": "user"|"assistant", "content": str | list}, ...].

        Returns:
            Modified messages list (deep copy) with cache_control added on the
            first large user-message block found scanning from the end, OR the
            original list if no suitable block was found or provider is not
            Anthropic.
        """
        if provider_name.lower() != "anthropic":
            return messages

        # Work on a deep copy so we never mutate caller state.
        msgs = copy.deepcopy(messages)

        # Scan user messages from the end to find the first one large enough to
        # be worth a cache breakpoint.  We cache from the start of the context up
        # to this message so the assembled codebase block is covered.
        for msg in reversed(msgs):
            if msg.get("role") != "user":
                continue
            content = msg.get("content", "")

            # Content is already a block list — inspect the last text block.
            if isinstance(content, list):
                # Find last text block
                for block in reversed(content):
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text", "")
                        if len(text) >= _MIN_CACHE_CHARS:
                            if "cache_control" not in block:
                                block["cache_control"] = {"type": "ephemeral"}
                            return msgs
                        # Found a text block but it's too small — stop searching
                        # this message and try the next one.
                        break
                continue

            # Content is a plain string — check length and convert to block list.
            if isinstance(content, str) and len(content) >= _MIN_CACHE_CHARS:
                msg["content"] = [
                    {
                        "type": "text",
                        "text": content,
                        "cache_control": {"type": "ephemeral"},
                    }
                ]
                return msgs

        # No suitable large block found — return the (still deep-copied) list.
        return msgs


# Module-level singleton so callers don't have to instantiate.
_wrapper = PromptCacheWrapper()


def add_cache_breakpoints(
    provider_name: str,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Module-level convenience wrapper around PromptCacheWrapper.

    Equivalent to ``PromptCacheWrapper().add_cache_breakpoints(...)``.
    """
    return _wrapper.add_cache_breakpoints(provider_name, messages)
