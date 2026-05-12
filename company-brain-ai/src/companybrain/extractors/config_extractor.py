"""
Config extractor — YAML / TOML / properties / .env — ADR-0057.

Emits one ConfigKey per leaf value, with a dotted path and a semantic tag
(from semantic_tags.tag_config_path). Deterministic, zero LLM cost.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

import yaml

from companybrain.extractors.base import Extractor
from companybrain.extractors.semantic_tags import tag_config_path
from companybrain.models.entities import ConfigKey, ExtractedBatch

try:
    import tomllib  # 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore

_YAML_SUFFIXES = frozenset({".yml", ".yaml"})
_TOML_SUFFIXES = frozenset({".toml"})
_PROPS_SUFFIXES = frozenset({".properties"})
_ENV_NAMES = frozenset({".env"})

# package.json / tsconfig.json / openapi.* are claimed by other extractors —
# this extractor handles "config-shaped JSON" only when nothing else does.
_GENERIC_JSON_SUFFIXES = frozenset({".json"})

# Files in this list are not config — they belong to manifest_extractor.
_NOT_CONFIG_NAMES = frozenset({
    "package.json", "package-lock.json", "tsconfig.json", "tsconfig.base.json",
    "composer.json",
})


class ConfigExtractor:
    kind = "config"

    def supports(self, path: Path) -> bool:
        name = path.name
        if name in _NOT_CONFIG_NAMES:
            return False
        if name.startswith(".env"):
            return True
        suffix = path.suffix.lower()
        if suffix in _YAML_SUFFIXES or suffix in _TOML_SUFFIXES or suffix in _PROPS_SUFFIXES:
            return True
        return False

    def extract(self, path: Path, content: str, *, repo: str = "") -> ExtractedBatch:
        suffix = path.suffix.lower()
        name = path.name
        pairs: list[tuple[str, str]] = []

        try:
            if suffix in _YAML_SUFFIXES:
                tree = yaml.safe_load(content)
                if isinstance(tree, dict):
                    pairs = list(_flatten(tree))
            elif suffix in _TOML_SUFFIXES:
                tree = tomllib.loads(content)
                pairs = list(_flatten(tree))
            elif suffix in _PROPS_SUFFIXES:
                pairs = list(_parse_properties(content))
            elif name.startswith(".env"):
                pairs = list(_parse_env(content))
        except Exception:
            # Malformed config — emit nothing rather than crashing the pipeline.
            pairs = []

        keys = [
            ConfigKey(
                file=str(path),
                repo=repo,
                path=p,
                value=v,
                semantic_tag=tag_config_path(p),
            )
            for p, v in pairs
        ]
        return ExtractedBatch(file=str(path), repo=repo, extractor_kind=self.kind, config_keys=keys)


def _flatten(tree: Any, prefix: str = "") -> Iterable[tuple[str, str]]:
    """Walk a nested dict/list and yield (dotted_path, stringified_leaf)."""
    if isinstance(tree, dict):
        for k, v in tree.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            yield from _flatten(v, key)
    elif isinstance(tree, list):
        for idx, item in enumerate(tree):
            key = f"{prefix}[{idx}]"
            yield from _flatten(item, key)
    else:
        yield prefix, "" if tree is None else str(tree)


def _parse_properties(content: str) -> Iterable[tuple[str, str]]:
    """Java .properties: ``key=value`` or ``key:value`` per line, # / ! comments."""
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        sep = _first_sep(line)
        if sep == -1:
            continue
        key = line[:sep].strip()
        val = line[sep + 1 :].strip()
        if key:
            yield key, val


def _first_sep(line: str) -> int:
    eq = line.find("=")
    co = line.find(":")
    if eq == -1:
        return co
    if co == -1:
        return eq
    return min(eq, co)


def _parse_env(content: str) -> Iterable[tuple[str, str]]:
    """``.env`` files: KEY=value, # comments, optional leading ``export``."""
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            yield key, val
