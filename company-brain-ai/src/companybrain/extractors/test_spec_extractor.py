"""
Test-as-spec extractor — ADR-0057 (stub).

The full extractor is LLM-bound (one batch per test class) and is left as a
follow-up. This stub keeps the kind reachable through the dispatcher so the
file_walker classifies test files correctly; ``extract()`` returns an empty
batch until the LLM path lands.
"""
from __future__ import annotations

from pathlib import Path

from companybrain.extractors.base import Extractor
from companybrain.models.entities import ExtractedBatch


_TEST_NAME_HINTS = ("Test", "Spec", "_test", ".test", "_spec", ".spec")


class TestSpecExtractor:
    kind = "test_spec"

    def supports(self, path: Path) -> bool:
        if path.suffix.lower() not in {".java", ".kt", ".py", ".ts", ".tsx", ".js", ".jsx"}:
            return False
        stem = path.stem
        return any(hint in stem for hint in _TEST_NAME_HINTS) or "tests" in path.parts or "test" in path.parts

    def extract(self, path: Path, content: str, *, repo: str = "") -> ExtractedBatch:
        # Deferred: LLM extraction of BehavioralSpec entities.
        return ExtractedBatch(file=str(path), repo=repo, extractor_kind=self.kind)
