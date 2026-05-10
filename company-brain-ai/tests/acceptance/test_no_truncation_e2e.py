"""
ADR-0047 acceptance test: no truncation end-to-end.

Creates a synthetic 120k-char source file (1 class, 30 methods, each referencing a
distinct DB column) and verifies that the chunker produces exactly 30 method chunks,
each carrying its full verbatim body.

Does NOT require a live database or LLM — tests the chunker layer only.
"""
from __future__ import annotations

import textwrap
import tempfile
from pathlib import Path

import pytest

from companybrain.pipeline.code_chunker import CodeChunker


def _build_synthetic_java_file(n_methods: int) -> str:
    """Generate a Java file with n_methods each referencing a unique column."""
    imports = "\n".join([
        "package com.example.synthetic;",
        "",
        "import java.util.List;",
        "import java.sql.Connection;",
        "",
    ])

    methods = []
    for i in range(n_methods):
        column = f"synthetic_column_{i:03d}"
        # Pad body to ~4000 chars so each method is clearly "large" enough to be
        # kept but small enough to stay as individual chunks.
        padding = f"// padding line {j}\n" * 50  # ~750 chars of padding
        body = textwrap.dedent(f"""\
            public List<String> fetchColumn{i:03d}(Connection conn) throws Exception {{
                // Reads column: {column}
                String sql = "SELECT {column} FROM synthetic_table WHERE id = ?";
                try (var stmt = conn.prepareStatement(sql)) {{
                    stmt.setLong(1, id);
                    var rs = stmt.executeQuery();
                    var result = new java.util.ArrayList<String>();
                    while (rs.next()) {{
                        result.add(rs.getString("{column}"));
                    }}
                    return result;
                }}
                {padding}
            }}
        """)
        methods.append(body)

    class_body = "\n".join(methods)
    full = (
        f"{imports}\n"
        f"public class SyntheticRepository {{\n"
        f"    private long id;\n\n"
        f"{class_body}"
        f"}}\n"
    )
    return full


@pytest.mark.acceptance
def test_chunker_produces_correct_chunk_count():
    """Chunker must produce exactly N method chunks for an N-method class."""
    n_methods = 30
    source = _build_synthetic_java_file(n_methods)

    assert len(source) > 100_000, f"Expected >100k chars, got {len(source)}"

    with tempfile.NamedTemporaryFile(suffix=".java", mode="w", delete=False) as f:
        f.write(source)
        tmp_path = f.name

    try:
        chunker = CodeChunker()
        chunks = chunker.chunk_file(tmp_path)
        method_chunks = [c for c in chunks if c.kind == "method"]

        assert len(method_chunks) == n_methods, (
            f"Expected {n_methods} method chunks, got {len(method_chunks)}. "
            f"qnames: {[c.qname for c in method_chunks]}"
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@pytest.mark.acceptance
def test_chunker_no_body_truncation():
    """Every chunk body must contain the full verbatim column reference."""
    n_methods = 30
    source = _build_synthetic_java_file(n_methods)

    with tempfile.NamedTemporaryFile(suffix=".java", mode="w", delete=False) as f:
        f.write(source)
        tmp_path = f.name

    try:
        chunker = CodeChunker()
        chunks = chunker.chunk_file(tmp_path)
        method_chunks = [c for c in chunks if c.kind == "method"]

        for i in range(n_methods):
            column = f"synthetic_column_{i:03d}"
            matching = [c for c in method_chunks if column in c.body]
            assert matching, (
                f"Column '{column}' not found verbatim in any chunk body — "
                "body was likely truncated"
            )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@pytest.mark.acceptance
def test_chunker_reads_full_file_length():
    """chunk_file() must read the full file, not a 6019-char truncated version."""
    n_methods = 30
    source = _build_synthetic_java_file(n_methods)
    assert len(source) != 6019, "Synthetic file accidentally hit truncation sentinel length"

    with tempfile.NamedTemporaryFile(suffix=".java", mode="w", delete=False) as f:
        f.write(source)
        tmp_path = f.name

    try:
        chunker = CodeChunker()
        chunks = chunker.chunk_file(tmp_path)
        total_body_chars = sum(len(c.body) for c in chunks)
        # Total body chars must cover a substantial fraction of the source file
        assert total_body_chars > len(source) * 0.5, (
            f"Total body chars {total_body_chars} is less than 50% of source {len(source)} "
            "— suggests content was truncated before chunking"
        )
    finally:
        Path(tmp_path).unlink(missing_ok=True)


@pytest.mark.acceptance
def test_truncated_content_raises():
    """chunk_file() must raise TruncatedContentError for known-sentinel lengths."""
    from companybrain.pipeline.code_chunker import TruncatedContentError

    sentinel_content = "x" * 6019  # exact sentinel length

    with tempfile.NamedTemporaryFile(suffix=".java", mode="w", delete=False) as f:
        f.write(sentinel_content)
        tmp_path = f.name

    try:
        chunker = CodeChunker()
        with pytest.raises(TruncatedContentError):
            chunker.chunk_file(tmp_path)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
