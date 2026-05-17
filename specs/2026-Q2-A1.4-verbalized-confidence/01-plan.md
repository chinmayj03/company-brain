# A1.4 Implementation Plan

## Architecture

```
query/orchestrator.py
  └─ after answer generation
       └─ build ConfidenceSignals from:
            - retrieval metadata (score, paths)
            - response (entity count, call_chain length)
            - verifier_result.score
            - entity timestamps (stub at 0.5 for now)
       └─ MultiSignalAggregator.aggregate(signals) → ConfidenceScore
       └─ response.confidence updated with value/label/rationale/signals
```

## Module layout

```
confidence/
  __init__.py         — re-exports
  signals.py          — ConfidenceSignals dataclass
  aggregator.py       — MultiSignalAggregator
  verbalizer.py       — scalar → label + rationale string
```

## Weights

| Signal             | Weight | Notes                                      |
|--------------------|--------|--------------------------------------------|
| retrieval_score    | 0.30   | Tunable via CONFIDENCE_WEIGHT_RETRIEVAL    |
| entity_match       | 0.20   | Normalized: min(count/5, 1.0)              |
| source_diversity   | 0.15   | fraction of unique file paths              |
| verifier_agreement | 0.20   | 1.0 = verified, 0.5 = not run, 0.0 = fail |
| chain_length       | 0.10   | Normalized: min(hops/5, 1.0)               |
| freshness          | 0.05   | Stubbed at 0.5 pending timestamp data      |

## Output schema extension

The existing `Confidence` model gains two optional fields:
- `value: float` — raw scalar
- `signals: dict` — raw signal values for debugging

The `level` field (existing) becomes the verbal label.
The `rationale` field (existing) becomes the generated rationale.

## Wiring point

`query/orchestrator.py` — after `ExplorationLoop.run()` returns an `AnswerResult`,
compute signals from the result + initial context metadata, call aggregator,
patch `result.response.confidence`.

`api/routes/query.py` — same patch after `_parse_llm_response()` for the
non-iterative path.
