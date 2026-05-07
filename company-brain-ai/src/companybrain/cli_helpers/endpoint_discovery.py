"""Discover HTTP endpoints in a repo using the same regexes as code_tracer.

Returns list of (METHOD, path) tuples. Order: controllers first, then any
route registrations found in TS/JS/Python.
"""
from __future__ import annotations
import re
from pathlib import Path

_JAVA_RE = re.compile(
    r'@(GetMapping|PostMapping|PutMapping|DeleteMapping|PatchMapping|RequestMapping)'
    r'\s*\(?[^)]*?(?:value\s*=\s*)?\{?\s*["\']([^"\']+)["\']'
)
_TS_RE = re.compile(
    r'(?:axios|fetch|api|http)\s*\.?\s*(get|post|put|delete|patch)\s*\(\s*[`\'"]([^`\'"]+)[\'"]'
)
_PY_RE = re.compile(
    r'@(?:router|app)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)["\']'
)


def discover_endpoints(repo_root: Path, *, max_endpoints: int = 100) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source in repo_root.rglob("*"):
        if not source.is_file():
            continue
        if source.suffix not in {".java", ".ts", ".tsx", ".js", ".jsx", ".py"}:
            continue
        if any(part in {"node_modules", ".git", "target", "build", "dist", "__pycache__"}
               for part in source.parts):
            continue
        try:
            text = source.read_text(errors="replace")
        except Exception:
            continue

        if source.suffix == ".java":
            for m in _JAVA_RE.finditer(text):
                method = _java_anno_to_method(m.group(1))
                _add(out, seen, (method, m.group(2)))
        elif source.suffix in {".ts", ".tsx", ".js", ".jsx"}:
            for m in _TS_RE.finditer(text):
                _add(out, seen, (m.group(1).upper(), m.group(2)))
        elif source.suffix == ".py":
            for m in _PY_RE.finditer(text):
                _add(out, seen, (m.group(1).upper(), m.group(2)))

        if len(out) >= max_endpoints:
            break
    return out


def _java_anno_to_method(anno: str) -> str:
    return anno.replace("Mapping", "").upper() if anno != "RequestMapping" else "GET"


def _add(lst: list, seen: set, pair: tuple) -> None:
    if pair not in seen:
        lst.append(pair)
        seen.add(pair)
