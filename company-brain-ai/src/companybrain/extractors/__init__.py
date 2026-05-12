"""
Universal File Extractors — ADR-0057.

Each module under this package implements a deterministic (or LLM-bound)
extractor for a class of files that the brain previously ignored:

  - doc_extractor       Markdown / AsciiDoc / RST / plain-text docs
  - config_extractor    YAML / TOML / properties / .env config files
  - manifest_extractor  POM, package.json, Cargo.toml, requirements.txt, ...
  - infra_extractor     Dockerfile, docker-compose, Makefile, Procfile, Terraform
  - ci_extractor        GitHub Actions / GitLab CI / Jenkins
  - javadoc_extractor   Javadoc / JSDoc / docstrings inside source files
  - test_spec_extractor BehavioralSpec entities (LLM-bound, stub for now)

The router in ``dispatch.py`` maps an extension (or filename pattern) to an
extractor instance. The shared contract lives in ``base.py``.
"""
from companybrain.extractors.base import Extractor
from companybrain.extractors.dispatch import (
    EXTRACTOR_DISPATCH,
    extractor_kind_for,
    get_extractor,
)

__all__ = ["Extractor", "EXTRACTOR_DISPATCH", "extractor_kind_for", "get_extractor"]
