"""Adaptive batching by token budget — replaces fixed-size ChunkBatcher."""
from companybrain.util.token_estimator import (
    estimate_output_tokens, fits_in_budget,
)


def pack_into_batches(
    chunks: list,
    max_output_tokens: int = 4_000,
    hard_max_per_batch: int = 16,
) -> list[list]:
    """Greedy first-fit packing.

    For each chunk, add it to the current batch IF the batch with this
    chunk added still fits in budget. Otherwise close the current batch
    and start a new one.

    Singletons that exceed budget alone get their own batch and will be
    handled by extraction_recovery's M3 fallback.
    """
    batches: list[list] = []
    current: list = []
    for c in chunks:
        if not current:
            current.append(c)
            continue
        if fits_in_budget(len(current) + 1, max_output_tokens) and len(current) < hard_max_per_batch:
            current.append(c)
        else:
            batches.append(current)
            current = [c]
    if current:
        batches.append(current)
    return batches
