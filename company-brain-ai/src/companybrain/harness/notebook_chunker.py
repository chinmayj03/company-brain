"""Jupyter (.ipynb) chunker — one MethodChunk per cell (ADR-0052 P6).

Notebooks are common in data-science repos but the language-agnostic
tree-sitter chunker can't parse them. We treat each cell as its own chunk
so downstream entity extraction sees per-cell context.

The chunker is intentionally permissive: cells with ``cell_type`` outside
``{code, markdown}`` (raw, etc.) are skipped silently rather than rejected
so an upgrade in nbformat that adds new cell types doesn't break us.

When ``nbformat`` isn't installed the chunker falls back to a hand-rolled
JSON read so notebook support still works on minimal images.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import structlog

from companybrain.pipeline.code_chunker import MethodChunk

log = structlog.get_logger(__name__)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()


def chunk_notebook(fp: Path | str) -> list[MethodChunk]:
    """Return one :class:`MethodChunk` per code/markdown cell.

    Each chunk's ``qname`` is ``<notebook-stem>.cell_<index>`` so URN paths
    sort by cell order; ``kind`` is ``"top_decl"`` (the code-chunker schema
    only has three kinds; cells fit best as top-level declarations) and
    ``language`` is ``python`` for code cells, ``markdown`` for markdown.
    """
    path = Path(fp)
    cells = _load_cells(path)
    out: list[MethodChunk] = []
    for i, cell in enumerate(cells):
        cell_type = cell.get("cell_type")
        if cell_type not in ("code", "markdown"):
            continue
        source = _join_source(cell.get("source", ""))
        if not source.strip():
            continue
        out.append(MethodChunk(
            file_path=str(path),
            qname=f"{path.stem}.cell_{i}",
            kind="top_decl",
            body=source,
            header_context=f"<cell index={i} type={cell_type}>",
            import_context="",
            body_hash=_sha256(source),
            language="python" if cell_type == "code" else "markdown",
        ))
    log.info("notebook_chunker.read", file=str(path), chunks=len(out))
    return out


# ── helpers ──────────────────────────────────────────────────────────────────

def _load_cells(path: Path) -> list[dict]:
    """Return the cell list. Prefers nbformat for forward compatibility."""
    try:
        import nbformat
    except ImportError:
        nbformat = None  # type: ignore[assignment]

    if nbformat is not None:
        try:
            nb = nbformat.read(str(path), as_version=4)
            return list(nb.cells or [])
        except Exception as exc:                # pragma: no cover — fallback path
            log.warning("notebook_chunker.nbformat_failed",
                        file=str(path), error=str(exc))

    # Fallback: parse the JSON directly. Notebooks are JSON with a
    # well-known top-level shape; the only quirk is that each cell's
    # ``source`` may be a string or a list of strings.
    try:
        data = json.loads(path.read_text(errors="ignore"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("notebook_chunker.read_failed",
                    file=str(path), error=str(exc))
        return []
    return list(data.get("cells", []))


def _join_source(src: str | list[str]) -> str:
    """Cell sources are sometimes a list of lines, sometimes a single string."""
    if isinstance(src, list):
        return "".join(src)
    return str(src)
