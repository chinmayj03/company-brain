"""
Build-manifest extractor — POM / npm / Cargo / pip / Go — ADR-0057.

Emits Dependency entities (and BuildPlugin for Maven). Deterministic parsers
only — every ecosystem has a stdlib-friendly format.
"""
from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

from companybrain.extractors.base import Extractor
from companybrain.models.entities import BuildPlugin, Dependency, ExtractedBatch

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


_POM_NAMES = frozenset({"pom.xml"})
_NPM_NAMES = frozenset({"package.json"})
_CARGO_NAMES = frozenset({"Cargo.toml"})
_GO_MOD_NAMES = frozenset({"go.mod"})
_PIP_NAMES = frozenset({"requirements.txt"})
_PIP_TOML_NAMES = frozenset({"pyproject.toml", "Pipfile"})


class ManifestExtractor:
    """One class, multiple ecosystems — dispatched on filename inside extract()."""

    kind = "manifest"

    def supports(self, path: Path) -> bool:
        name = path.name
        return (
            name in _POM_NAMES
            or name in _NPM_NAMES
            or name in _CARGO_NAMES
            or name in _GO_MOD_NAMES
            or name in _PIP_NAMES
            or name in _PIP_TOML_NAMES
        )

    def extract(self, path: Path, content: str, *, repo: str = "") -> ExtractedBatch:
        name = path.name
        deps: list[Dependency] = []
        plugins: list[BuildPlugin] = []

        try:
            if name in _POM_NAMES:
                deps, plugins = _parse_pom(content, file=str(path), repo=repo)
            elif name in _NPM_NAMES:
                deps = _parse_npm(content, file=str(path), repo=repo)
            elif name in _CARGO_NAMES:
                deps = _parse_cargo(content, file=str(path), repo=repo)
            elif name in _GO_MOD_NAMES:
                deps = _parse_go_mod(content, file=str(path), repo=repo)
            elif name in _PIP_NAMES:
                deps = _parse_requirements(content, file=str(path), repo=repo)
            elif name in _PIP_TOML_NAMES:
                deps = _parse_pyproject(content, file=str(path), repo=repo)
        except Exception:
            deps, plugins = [], []

        return ExtractedBatch(
            file=str(path),
            repo=repo,
            extractor_kind=self.kind,
            dependencies=deps,
            build_plugins=plugins,
        )


def _strip_ns(tag: str) -> str:
    """Maven POMs use a namespace — strip ``{http://...}artifactId`` → ``artifactId``."""
    return tag.split("}", 1)[1] if "}" in tag else tag


def _parse_pom(content: str, *, file: str, repo: str) -> tuple[list[Dependency], list[BuildPlugin]]:
    root = ET.fromstring(content)
    deps: list[Dependency] = []
    plugins: list[BuildPlugin] = []

    # iter() walks all descendants; check tag name only (namespace-agnostic)
    for el in root.iter():
        tag = _strip_ns(el.tag)
        if tag == "dependency":
            gid = _child_text(el, "groupId")
            aid = _child_text(el, "artifactId")
            ver = _child_text(el, "version")
            scope = _child_text(el, "scope")
            if aid:
                name = f"{gid}:{aid}" if gid else aid
                deps.append(Dependency(
                    file=file, repo=repo, name=name, version=ver,
                    scope=scope, ecosystem="maven",
                ))
        elif tag == "plugin":
            gid = _child_text(el, "groupId")
            aid = _child_text(el, "artifactId")
            ver = _child_text(el, "version")
            if aid:
                name = f"{gid}:{aid}" if gid else aid
                plugins.append(BuildPlugin(file=file, repo=repo, name=name, version=ver))

    return deps, plugins


def _child_text(el: ET.Element, name: str) -> str | None:
    for child in el:
        if _strip_ns(child.tag) == name:
            return (child.text or "").strip() or None
    return None


def _parse_npm(content: str, *, file: str, repo: str) -> list[Dependency]:
    data = json.loads(content)
    out: list[Dependency] = []
    for bucket, scope in (
        ("dependencies", "runtime"),
        ("devDependencies", "dev"),
        ("peerDependencies", "peer"),
        ("optionalDependencies", "optional"),
    ):
        for nm, ver in (data.get(bucket) or {}).items():
            out.append(Dependency(
                file=file, repo=repo, name=nm, version=str(ver),
                scope=scope, ecosystem="npm",
            ))
    return out


def _parse_cargo(content: str, *, file: str, repo: str) -> list[Dependency]:
    data = tomllib.loads(content)
    out: list[Dependency] = []
    for bucket, scope in (("dependencies", "runtime"), ("dev-dependencies", "dev"),
                          ("build-dependencies", "build")):
        for nm, spec in (data.get(bucket) or {}).items():
            ver = spec if isinstance(spec, str) else (spec.get("version") if isinstance(spec, dict) else None)
            out.append(Dependency(
                file=file, repo=repo, name=nm, version=ver,
                scope=scope, ecosystem="cargo",
            ))
    return out


_GO_REQUIRE = re.compile(r"^\s*([^\s]+)\s+([^\s]+)", re.MULTILINE)


def _parse_go_mod(content: str, *, file: str, repo: str) -> list[Dependency]:
    out: list[Dependency] = []
    # Single-line: ``require example.com/foo v1.2.3``
    for m in re.finditer(r"^\s*require\s+([^\s]+)\s+([^\s]+)", content, re.MULTILINE):
        out.append(Dependency(
            file=file, repo=repo, name=m.group(1), version=m.group(2),
            scope="runtime", ecosystem="go",
        ))
    # Block: ``require ( foo v1.0.0 bar v2.0.0 )``
    for block_match in re.finditer(r"require\s*\(([^)]*)\)", content, re.DOTALL):
        for line_match in _GO_REQUIRE.finditer(block_match.group(1)):
            nm = line_match.group(1)
            ver = line_match.group(2)
            if nm == "require":  # the keyword itself can match if regex caught it
                continue
            out.append(Dependency(
                file=file, repo=repo, name=nm, version=ver,
                scope="runtime", ecosystem="go",
            ))
    return out


_REQ_LINE = re.compile(r"^\s*([A-Za-z0-9_.\-]+)\s*([<>=!~]=?)\s*([A-Za-z0-9_.\-+*]+)?")


def _parse_requirements(content: str, *, file: str, repo: str) -> list[Dependency]:
    out: list[Dependency] = []
    for raw in content.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-") or line.startswith("#"):
            continue
        m = _REQ_LINE.match(line)
        if not m:
            # bare ``pkg`` with no version is still valid
            name = line.split("[")[0].strip()
            if name and re.match(r"^[A-Za-z0-9_.\-]+$", name):
                out.append(Dependency(
                    file=file, repo=repo, name=name, version=None,
                    scope="runtime", ecosystem="pip",
                ))
            continue
        nm = m.group(1)
        ver = m.group(3) if m.group(2) and m.group(3) else None
        out.append(Dependency(
            file=file, repo=repo, name=nm, version=ver,
            scope="runtime", ecosystem="pip",
        ))
    return out


def _parse_pyproject(content: str, *, file: str, repo: str) -> list[Dependency]:
    data = tomllib.loads(content)
    out: list[Dependency] = []
    # PEP 621
    project = data.get("project") or {}
    for entry in project.get("dependencies", []) or []:
        nm, ver = _split_pep508(entry)
        out.append(Dependency(file=file, repo=repo, name=nm, version=ver, scope="runtime", ecosystem="pip"))
    for group, entries in (project.get("optional-dependencies") or {}).items():
        for entry in entries:
            nm, ver = _split_pep508(entry)
            out.append(Dependency(file=file, repo=repo, name=nm, version=ver, scope=group, ecosystem="pip"))
    # Poetry [tool.poetry.dependencies]
    poetry = (data.get("tool") or {}).get("poetry") or {}
    for nm, spec in (poetry.get("dependencies") or {}).items():
        ver = spec if isinstance(spec, str) else (spec.get("version") if isinstance(spec, dict) else None)
        out.append(Dependency(file=file, repo=repo, name=nm, version=ver, scope="runtime", ecosystem="pip"))
    for nm, spec in (poetry.get("dev-dependencies") or {}).items():
        ver = spec if isinstance(spec, str) else (spec.get("version") if isinstance(spec, dict) else None)
        out.append(Dependency(file=file, repo=repo, name=nm, version=ver, scope="dev", ecosystem="pip"))
    return out


def _split_pep508(entry: str) -> tuple[str, str | None]:
    m = _REQ_LINE.match(entry)
    if not m:
        nm = entry.split("[")[0].strip()
        return nm, None
    return m.group(1), (m.group(3) if m.group(2) and m.group(3) else None)
