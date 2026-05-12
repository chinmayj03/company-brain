"""
Extractor dispatch — maps a file path to the right Extractor instance — ADR-0057.

Lookup order:
  1. Exact filename match (Dockerfile, pom.xml, Jenkinsfile, ...)
  2. Filename-prefix match (Dockerfile.*, docker-compose.*)
  3. Path-pattern match (.github/workflows/*.yml)
  4. Suffix match (.md, .yml, .toml, ...)

Each ``Extractor.supports(path)`` provides the final yes/no — the table below
narrows the candidate set; ``supports`` is the ground truth.

ADR-0058 (schema-aware extraction) appends to ``_SCHEMA_EXTRACTORS`` rather
than mutating the existing tables, keeping merges trivial.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from companybrain.extractors.base import Extractor
from companybrain.extractors.ci_extractor import CIExtractor
from companybrain.extractors.config_extractor import ConfigExtractor
from companybrain.extractors.doc_extractor import DocExtractor
from companybrain.extractors.infra_extractor import InfraExtractor
from companybrain.extractors.javadoc_extractor import JavadocExtractor
from companybrain.extractors.manifest_extractor import ManifestExtractor
from companybrain.extractors.test_spec_extractor import TestSpecExtractor


_DOC = DocExtractor()
_CONFIG = ConfigExtractor()
_MANIFEST = ManifestExtractor()
_INFRA = InfraExtractor()
_CI = CIExtractor()
_JAVADOC = JavadocExtractor()
_TEST_SPEC = TestSpecExtractor()

# Public alias for callers that want to enumerate / introspect.
EXTRACTOR_DISPATCH: tuple[Extractor, ...] = (
    _CI,         # most specific first — .github/workflows/foo.yml is CI not config
    _MANIFEST,
    _INFRA,
    _CONFIG,
    _DOC,
    _TEST_SPEC,
    _JAVADOC,
)


# ── ADR-0058 schema extractors append here ────────────────────────────────────
_SCHEMA_EXTRACTORS: list[Extractor] = []


def register_schema_extractor(extractor: Extractor) -> None:
    """Used by ADR-0058 to append schema-aware extractors without touching this file."""
    _SCHEMA_EXTRACTORS.append(extractor)


def get_extractor(path: Path) -> Optional[Extractor]:
    """Return the first Extractor whose ``supports()`` is True, or None."""
    for ex in EXTRACTOR_DISPATCH:
        if ex.supports(path):
            return ex
    for ex in _SCHEMA_EXTRACTORS:
        if ex.supports(path):
            return ex
    return None


def extractor_kind_for(path: Path) -> Optional[str]:
    ex = get_extractor(path)
    return ex.kind if ex else None
