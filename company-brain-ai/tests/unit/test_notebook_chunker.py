"""Unit tests for the Jupyter chunker (ADR-0052 P6)."""
from __future__ import annotations

import json
from pathlib import Path

from companybrain.harness.notebook_chunker import chunk_notebook
from companybrain.pipeline.code_chunker import CodeChunker


def _write_notebook(path: Path, cells: list[dict]) -> None:
    path.write_text(json.dumps({
        "metadata": {"language_info": {"name": "python"}},
        "nbformat": 4,
        "nbformat_minor": 5,
        "cells": cells,
    }))


def test_chunk_notebook_emits_one_chunk_per_cell(tmp_path: Path):
    """Each code/markdown cell becomes its own MethodChunk."""
    nb = tmp_path / "demo.ipynb"
    _write_notebook(nb, [
        {"cell_type": "markdown", "source": "# title\n", "metadata": {}},
        {"cell_type": "code", "source": "x = 1\n", "metadata": {}, "outputs": [], "execution_count": 1},
        {"cell_type": "code", "source": "y = x + 2\n", "metadata": {}, "outputs": [], "execution_count": 2},
    ])

    chunks = chunk_notebook(nb)
    assert len(chunks) == 3
    assert [c.qname for c in chunks] == ["demo.cell_0", "demo.cell_1", "demo.cell_2"]
    assert [c.language for c in chunks] == ["markdown", "python", "python"]
    assert chunks[1].body == "x = 1\n"


def test_chunk_notebook_skips_unknown_cell_types(tmp_path: Path):
    """Raw cells (and any future type) are skipped silently."""
    nb = tmp_path / "raw.ipynb"
    _write_notebook(nb, [
        {"cell_type": "raw", "source": "ignore me", "metadata": {}},
        {"cell_type": "code", "source": "z = 0\n", "metadata": {}, "outputs": [], "execution_count": 1},
    ])

    chunks = chunk_notebook(nb)
    assert len(chunks) == 1
    assert chunks[0].language == "python"


def test_chunk_notebook_skips_empty_cells(tmp_path: Path):
    """Whitespace-only cells contribute nothing."""
    nb = tmp_path / "empty.ipynb"
    _write_notebook(nb, [
        {"cell_type": "code", "source": "", "metadata": {}, "outputs": [], "execution_count": 1},
        {"cell_type": "code", "source": "   \n", "metadata": {}, "outputs": [], "execution_count": 2},
        {"cell_type": "code", "source": "x = 1\n", "metadata": {}, "outputs": [], "execution_count": 3},
    ])

    chunks = chunk_notebook(nb)
    assert len(chunks) == 1


def test_chunk_notebook_handles_list_source(tmp_path: Path):
    """Cell ``source`` may be a list of strings; we join them transparently."""
    nb = tmp_path / "listsrc.ipynb"
    _write_notebook(nb, [
        {"cell_type": "code", "source": ["import math\n", "print(math.pi)\n"],
         "metadata": {}, "outputs": [], "execution_count": 1},
    ])

    chunks = chunk_notebook(nb)
    assert len(chunks) == 1
    assert chunks[0].body == "import math\nprint(math.pi)\n"


def test_code_chunker_delegates_ipynb_to_notebook_chunker(tmp_path: Path):
    """``CodeChunker.chunk_file`` must hand .ipynb files to the notebook path.

    This is what makes ``brain index`` produce notebook-cell entities without
    further glue — the orchestrator already iterates code_units through
    chunk_file().
    """
    nb = tmp_path / "auto.ipynb"
    _write_notebook(nb, [
        {"cell_type": "code", "source": "a = 1\n", "metadata": {}, "outputs": [], "execution_count": 1},
        {"cell_type": "code", "source": "b = a + 1\n", "metadata": {}, "outputs": [], "execution_count": 2},
    ])

    chunks = CodeChunker().chunk_file(str(nb))
    assert len(chunks) == 2
    assert chunks[0].qname == "auto.cell_0"
    assert chunks[1].qname == "auto.cell_1"
