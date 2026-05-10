"""Token estimator calibrated against historical extraction outputs.

Calibration was done offline on 50 prior extractions; constants live
here as named values so the test suite can assert against them.
"""

# Empirical: each extracted entity costs ~250 output tokens (method body
# entity + 21-field BusinessContext). Each edge adds ~60 tokens. Average
# is ~2.5 edges per method. Envelope overhead is ~200 tokens per call.
TOKENS_PER_ENTITY     = 250
TOKENS_PER_EDGE       = 60
AVG_EDGES_PER_METHOD  = 2.5
ENVELOPE_OVERHEAD     = 200
SAFETY_MARGIN         = 0.8     # use only 80% of max_tokens to leave room for under-estimates


def estimate_output_tokens(num_chunks: int) -> int:
    return (
        num_chunks * (TOKENS_PER_ENTITY + int(AVG_EDGES_PER_METHOD * TOKENS_PER_EDGE))
        + ENVELOPE_OVERHEAD
    )


def estimate_input_tokens(chunks: list) -> int:
    """Char-based approximation: 4 chars ≈ 1 token (Anthropic guidance)."""
    total_chars = sum(
        len(c.body or "") + len(getattr(c, "header_context", None) or "") + len(getattr(c, "import_context", None) or "")
        for c in chunks
    )
    return total_chars // 4


def fits_in_budget(num_chunks: int, max_tokens: int) -> bool:
    return estimate_output_tokens(num_chunks) <= int(max_tokens * SAFETY_MARGIN)
