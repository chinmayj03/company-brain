# A1.4 Tasks

- [x] Create `confidence/signals.py` — ConfidenceSignals dataclass
- [x] Create `confidence/aggregator.py` — MultiSignalAggregator
- [x] Create `confidence/verbalizer.py` — scalar → label + rationale
- [x] Create `confidence/__init__.py` — re-exports
- [x] Extend `Confidence` model with `value` + `signals` optional fields
- [x] Add CONFIDENCE_WEIGHTS_* tunables to `config.py`
- [x] Wire aggregator into `query/orchestrator.py`
- [x] Wire aggregator into `api/routes/query.py` (non-iterative path)
- [x] Unit tests: `test_confidence_aggregator.py`
- [x] Unit tests: `test_confidence_verbalizer.py`
