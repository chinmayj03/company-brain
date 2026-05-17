# A1.4 Verbalized Confidence + Multi-Signal Aggregator

## Problem

The current `Confidence` model in `QueryResponse` is LLM-produced: the model
is told to output `{"level": "high|medium|low", "rationale": "..."}` and it
does so inconsistently, often just writing `"high"` for everything. There are
no objective signals feeding confidence — it's pure vibes.

## Goal

Replace the LLM-hallucinated confidence stub with a deterministic
multi-signal aggregator that:

1. Collects 6 objective signals after retrieval + answer generation
2. Weights them into a scalar 0.0–1.0
3. Buckets to a verbal label (high / medium / low)
4. Generates a rationale string from actual signal values (not a template)
5. Exposes raw signal values in the response for UI / debugging

## Non-goals

- Changing the `Confidence` Pydantic model (for backward compat we extend it)
- Replacing the LLM-produced answer itself (aggregator only affects `confidence`)

## Acceptance criteria

- [ ] `ConfidenceSignals` captures all 6 signals
- [ ] `MultiSignalAggregator` unit tests verify weights produce correct scalar
- [ ] `Verbalizer` correctly maps ≥0.8 → high, 0.55–0.79 → medium, <0.55 → low
- [ ] Query response has `confidence.value`, `confidence.label`, `confidence.rationale`
- [ ] Rationale text references actual signal values (not template)
- [ ] Existing query tests pass unchanged
- [ ] Config tunables working (change a weight → aggregator uses new weight)
