"""Unit tests for ADR-0050 M1 token estimator."""
import pytest
from companybrain.util.token_estimator import (
    TOKENS_PER_ENTITY,
    TOKENS_PER_EDGE,
    AVG_EDGES_PER_METHOD,
    ENVELOPE_OVERHEAD,
    SAFETY_MARGIN,
    estimate_output_tokens,
    estimate_input_tokens,
    fits_in_budget,
)


def test_constants_are_calibrated():
    assert TOKENS_PER_ENTITY == 250
    assert TOKENS_PER_EDGE == 60
    assert AVG_EDGES_PER_METHOD == 2.5
    assert ENVELOPE_OVERHEAD == 200
    assert SAFETY_MARGIN == 0.8


def test_estimate_output_tokens_single():
    # 1 chunk → 250 + int(2.5*60) + 200 = 250 + 150 + 200 = 600
    result = estimate_output_tokens(1)
    assert result == 600


def test_estimate_output_tokens_batch():
    # 8 chunks → 8 * (250+150) + 200 = 3200 + 200 = 3400
    result = estimate_output_tokens(8)
    assert result == 3400


def test_estimate_output_tokens_zero():
    result = estimate_output_tokens(0)
    assert result == ENVELOPE_OVERHEAD


def test_estimate_input_tokens():
    class FakeChunk:
        body = "a" * 400
        header_context = "b" * 100
        import_context = "c" * 100

    chunks = [FakeChunk(), FakeChunk()]
    # total chars = 2 * (400+100+100) = 1200; 1200//4 = 300
    result = estimate_input_tokens(chunks)
    assert result == 300


def test_estimate_input_tokens_missing_optional_fields():
    class MinimalChunk:
        body = "x" * 400
        # no header_context or import_context

    chunks = [MinimalChunk()]
    result = estimate_input_tokens(chunks)
    assert result == 400 // 4


def test_fits_in_budget_small_batch():
    # 1 chunk = 600 tokens; budget 4000 → 0.8*4000=3200; 600<=3200 → True
    assert fits_in_budget(1, 4_000) is True


def test_fits_in_budget_large_batch():
    # 64 chunks → 64*(250+150)+200 = 25800; budget 4000 → 3200; 25800>3200 → False
    assert fits_in_budget(64, 4_000) is False


def test_fits_in_budget_boundary():
    # find max chunks that fit in 4000: (4000*0.8 - 200) / 400 = 7 chunks → 3000 ≤ 3200
    assert fits_in_budget(7, 4_000) is True
    # 8 chunks → 3400 > 3200 → False
    assert fits_in_budget(8, 4_000) is False
