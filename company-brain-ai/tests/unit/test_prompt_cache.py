"""
Unit tests for companybrain.llm.prompt_cache.PromptCacheWrapper (A1.3).

Verifies:
  - Breakpoints are added to large user-message content on anthropic provider.
  - Breakpoints are NOT added for non-anthropic providers.
  - Breakpoints are NOT added when the message is below the size threshold.
  - The original messages list is not mutated (deep-copy guarantee).
  - The module-level convenience function works identically.
"""
from __future__ import annotations

import pytest

from companybrain.llm.prompt_cache import PromptCacheWrapper, add_cache_breakpoints, _MIN_CACHE_CHARS


# ── Helpers ────────────────────────────────────────────────────────────────────

def _large_text(extra: int = 0) -> str:
    """Return a string that is at least _MIN_CACHE_CHARS chars long."""
    return "x" * (_MIN_CACHE_CHARS + extra)


def _small_text() -> str:
    """Return a string that is definitely below _MIN_CACHE_CHARS chars."""
    return "short context"


def _user_msg(content) -> dict:
    return {"role": "user", "content": content}


def _assistant_msg(text: str) -> dict:
    return {"role": "assistant", "content": text}


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestPromptCacheWrapper:

    def setup_method(self):
        self.wrapper = PromptCacheWrapper()

    # ── Basic breakpoint insertion (anthropic provider) ────────────────────────

    def test_adds_breakpoint_on_anthropic_large_string_content(self):
        """Large string content is converted to a block list with cache_control."""
        messages = [_user_msg(_large_text())]
        result = self.wrapper.add_cache_breakpoints("anthropic", messages)

        assert len(result) == 1
        content = result[0]["content"]
        assert isinstance(content, list), "content should be converted to block list"
        assert len(content) == 1
        block = content[0]
        assert block["type"] == "text"
        assert block.get("cache_control") == {"type": "ephemeral"}

    def test_adds_breakpoint_on_anthropic_large_block_list(self):
        """When content is already a block list, cache_control is added to the last text block."""
        messages = [
            _user_msg([
                {"type": "text", "text": _large_text()},
            ])
        ]
        result = self.wrapper.add_cache_breakpoints("anthropic", messages)
        block = result[0]["content"][-1]
        assert block.get("cache_control") == {"type": "ephemeral"}

    def test_adds_breakpoint_to_last_large_user_message(self):
        """When multiple user messages exist, the last large one gets the breakpoint."""
        messages = [
            _user_msg(_large_text()),            # first large user message
            _assistant_msg("ok"),
            _user_msg(_large_text(extra=100)),   # second (last) large user message
        ]
        result = self.wrapper.add_cache_breakpoints("anthropic", messages)

        # The LAST user message (index 2) should have the breakpoint.
        last_content = result[2]["content"]
        assert isinstance(last_content, list)
        assert last_content[-1].get("cache_control") == {"type": "ephemeral"}

        # The first user message should NOT have a breakpoint (we stop after the first match
        # when scanning in reverse — that first match is the last message).
        first_content = result[0]["content"]
        # It may still be a plain string (no block conversion needed for the non-targeted msg)
        if isinstance(first_content, list):
            for block in first_content:
                assert "cache_control" not in block
        # If it stayed as a string, that's also correct.

    # ── Non-anthropic providers ────────────────────────────────────────────────

    def test_no_op_for_openai_provider(self):
        messages = [_user_msg(_large_text())]
        result = self.wrapper.add_cache_breakpoints("openai", messages)
        # Must return the exact same object (no copy).
        assert result is messages
        # Content must be unchanged.
        assert isinstance(result[0]["content"], str)

    def test_no_op_for_ollama_provider(self):
        messages = [_user_msg(_large_text())]
        result = self.wrapper.add_cache_breakpoints("ollama", messages)
        assert result is messages

    def test_no_op_for_empty_provider(self):
        messages = [_user_msg(_large_text())]
        result = self.wrapper.add_cache_breakpoints("", messages)
        assert result is messages

    def test_provider_check_is_case_insensitive(self):
        """'Anthropic' (mixed-case) should still trigger breakpoints."""
        messages = [_user_msg(_large_text())]
        result = self.wrapper.add_cache_breakpoints("Anthropic", messages)
        assert result is not messages  # a copy was made
        content = result[0]["content"]
        assert isinstance(content, list)
        assert content[0].get("cache_control") == {"type": "ephemeral"}

    # ── Small messages (below threshold) ──────────────────────────────────────

    def test_no_breakpoint_on_small_string_content(self):
        """Short messages don't get cache_control — they're below Anthropic's min."""
        messages = [_user_msg(_small_text())]
        result = self.wrapper.add_cache_breakpoints("anthropic", messages)
        # Content stays as a string when it's too small to break.
        content = result[0]["content"]
        assert isinstance(content, str) or (
            isinstance(content, list) and all("cache_control" not in b for b in content)
        )

    def test_no_breakpoint_on_small_block_list(self):
        messages = [
            _user_msg([{"type": "text", "text": _small_text()}])
        ]
        result = self.wrapper.add_cache_breakpoints("anthropic", messages)
        block = result[0]["content"][0]
        assert "cache_control" not in block

    # ── Immutability / deep-copy ───────────────────────────────────────────────

    def test_does_not_mutate_original_messages(self):
        """The original messages list must not be modified."""
        original_content = _large_text()
        messages = [_user_msg(original_content)]
        _ = self.wrapper.add_cache_breakpoints("anthropic", messages)

        # Original must still be a plain string.
        assert messages[0]["content"] == original_content

    def test_idempotent_block_list(self):
        """Running add_cache_breakpoints twice does not add duplicate breakpoints."""
        messages = [_user_msg(_large_text())]
        once = self.wrapper.add_cache_breakpoints("anthropic", messages)
        twice = self.wrapper.add_cache_breakpoints("anthropic", once)
        block = twice[0]["content"][-1]
        # cache_control should exist exactly once — still {"type": "ephemeral"}
        assert block.get("cache_control") == {"type": "ephemeral"}

    # ── Empty / edge cases ─────────────────────────────────────────────────────

    def test_empty_messages_list(self):
        result = self.wrapper.add_cache_breakpoints("anthropic", [])
        assert result == []

    def test_only_assistant_messages(self):
        """No user messages → no breakpoint added, returns copy."""
        messages = [_assistant_msg(_large_text())]
        result = self.wrapper.add_cache_breakpoints("anthropic", messages)
        assert result[0]["role"] == "assistant"
        assert "cache_control" not in result[0]

    # ── Module-level convenience function ─────────────────────────────────────

    def test_module_level_function_delegates_to_wrapper(self):
        messages = [_user_msg(_large_text())]
        result = add_cache_breakpoints("anthropic", messages)
        content = result[0]["content"]
        assert isinstance(content, list)
        assert content[0].get("cache_control") == {"type": "ephemeral"}

    def test_module_level_function_no_op_for_openai(self):
        messages = [_user_msg(_large_text())]
        result = add_cache_breakpoints("openai", messages)
        assert result is messages
