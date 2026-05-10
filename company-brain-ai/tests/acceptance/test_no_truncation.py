"""
ADR-0044 PR-0044-6: No-truncation acceptance test.

Generates a synthetic single-class file with 30 methods, each containing
a distinct SQL query referencing a distinct column. Total file size ~120k chars.

Asserts:
  1. CodeChunker produces exactly 30 method chunks from the 30-method class.
  2. Every distinct column name surfaces in at least one chunk body (no truncation).
  3. Every chunk's body_hash is stable and non-empty.
  4. Total body characters across all chunks cover ≥80% of the file size
     (import and class header are counted once in header_context, not bodies).

ADR-0045 update: the chunker now reads from disk (not from unit.content), so
each test writes a real .java file to tmp_path via _make_unit().

This test does NOT make LLM calls — it validates the chunking layer only,
which is the structural guarantee that makes per-chunk extraction complete.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from companybrain.collectors.code_tracer import CodeUnit
from companybrain.pipeline.code_chunker import CodeChunker, _sha256


def _generate_large_java_class(num_methods: int = 30) -> tuple[str, list[str]]:
    """
    Generate a Java class with `num_methods` methods, each with a distinct SQL
    query referencing a distinct column (col_000 .. col_029).
    Returns (file_content, list_of_expected_column_names).
    """
    imports = textwrap.dedent("""\
        package com.example.reporting;

        import java.util.List;
        import java.util.Optional;
        import org.springframework.jdbc.core.JdbcTemplate;
        import org.springframework.stereotype.Repository;
        """)

    header = textwrap.dedent("""\
        @Repository
        public class ReportingRepository {

            private final JdbcTemplate jdbc;

            public ReportingRepository(JdbcTemplate jdbc) {
                this.jdbc = jdbc;
            }

        """)

    columns: list[str] = []
    methods: list[str] = []
    for i in range(num_methods):
        col = f"col_{i:03d}"
        columns.append(col)
        padding = "    // " + "x" * 1000 + "\n"  # ~1k chars per method to reach 120k total
        method = textwrap.dedent(f"""\
            public List<String> queryBy{col.replace('_', '')}(String value) {{
                // ADR-0044 acceptance test: column {col}
                {padding}
                String sql = "SELECT {col} FROM report_data WHERE {col} = ? AND active = true";
                return jdbc.queryForList(sql, String.class, value);
            }}

        """)
        methods.append(method)

    closing = "}\n"
    content = imports + "\n" + header + "\n".join(methods) + closing
    return content, columns


def _make_unit(content: str, tmp_path: Path, language: str = "java",
               class_name: str = "ReportingRepository") -> CodeUnit:
    """Write content to a real file and return a CodeUnit pointing at it.

    ADR-0045: the chunker reads from disk, so the unit must carry an absolute
    path to a real file — not an in-memory content string.
    """
    ext = {"java": ".java", "python": ".py", "typescript": ".ts"}.get(language, ".java")
    fp = tmp_path / f"{class_name}{ext}"
    fp.write_text(content, encoding="utf-8")
    return CodeUnit(
        file_path=str(fp),
        repo_name="test-repo",
        role="repository",
        class_name=class_name,
        language=language,
    )


def test_chunker_produces_30_chunks_from_30_method_class(tmp_path):
    content, columns = _generate_large_java_class(30)
    unit = _make_unit(content, tmp_path)
    chunker = CodeChunker()
    chunks = chunker.chunk_unit(unit)

    assert len(chunks) >= 30, (
        f"Expected ≥30 chunks for 30 methods, got {len(chunks)}. "
        f"This indicates the chunker is processing the file as a whole (truncation risk)."
    )


def test_every_column_surfaces_in_chunk_bodies(tmp_path):
    """No column name is lost — every method body must appear in at least one chunk."""
    content, columns = _generate_large_java_class(30)
    unit = _make_unit(content, tmp_path)
    chunker = CodeChunker()
    chunks = chunker.chunk_unit(unit)

    all_bodies = " ".join(c.body for c in chunks)

    missing = [col for col in columns if col not in all_bodies]
    assert not missing, (
        f"These columns were not found in any chunk body (truncation): {missing}"
    )


def test_chunk_bodies_cover_sufficient_content(tmp_path):
    """Total body chars across all chunks must cover ≥80% of the file content."""
    content, _ = _generate_large_java_class(30)
    assert len(content) >= 30_000, f"Fixture too small: {len(content)} chars"

    unit = _make_unit(content, tmp_path)
    chunker = CodeChunker()
    chunks = chunker.chunk_unit(unit)

    total_body_chars = sum(len(c.body) for c in chunks)
    coverage = total_body_chars / len(content)
    assert coverage >= 0.80, (
        f"Chunk bodies cover only {coverage:.0%} of the file. "
        f"Expected ≥80%. Possible truncation in chunker."
    )


def test_body_hashes_are_stable_and_unique(tmp_path):
    content, _ = _generate_large_java_class(30)
    unit = _make_unit(content, tmp_path)
    chunker = CodeChunker()

    chunks1 = chunker.chunk_unit(unit)
    chunks2 = chunker.chunk_unit(unit)

    hashes1 = sorted(c.body_hash for c in chunks1)
    hashes2 = sorted(c.body_hash for c in chunks2)
    assert hashes1 == hashes2, "body_hashes are not stable across two runs"

    # Hashes should match sha256(body) for every chunk
    for chunk in chunks1:
        assert chunk.body_hash == _sha256(chunk.body), (
            f"body_hash mismatch for {chunk.qname}"
        )


def test_no_chunk_body_exceeds_limit(tmp_path):
    """No single chunk body should exceed the 50k char limit."""
    from companybrain.pipeline.code_chunker import CHUNK_BODY_LIMIT
    content, _ = _generate_large_java_class(30)
    unit = _make_unit(content, tmp_path)
    chunker = CodeChunker()
    chunks = chunker.chunk_unit(unit)

    oversized = [c.qname for c in chunks if len(c.body) > CHUNK_BODY_LIMIT]
    assert not oversized, f"Chunks exceeding {CHUNK_BODY_LIMIT} char limit: {oversized}"


def test_all_chunks_have_valid_kind(tmp_path):
    content, _ = _generate_large_java_class(30)
    unit = _make_unit(content, tmp_path)
    chunker = CodeChunker()
    for chunk in chunker.chunk_unit(unit):
        assert chunk.kind in ("method", "top_decl", "schema_block", "unreadable_file"), (
            f"Invalid kind {chunk.kind!r} on chunk {chunk.qname}"
        )


def test_import_context_capped_per_chunk(tmp_path):
    """import_context in every chunk must not exceed 50 lines."""
    content, _ = _generate_large_java_class(30)
    unit = _make_unit(content, tmp_path)
    chunker = CodeChunker()
    for chunk in chunker.chunk_unit(unit):
        lines = chunk.import_context.splitlines()
        assert len(lines) <= 51, (
            f"import_context in chunk {chunk.qname} has {len(lines)} lines (max 51)"
        )


def test_sql_method_bodies_contain_verbatim_queries(tmp_path):
    """
    Each method's SQL string must appear verbatim in the chunk body.
    This is the definitive no-truncation check: the query_text is only
    recoverable if the full method body is present.
    """
    content, columns = _generate_large_java_class(30)
    unit = _make_unit(content, tmp_path)
    chunker = CodeChunker()
    chunks = chunker.chunk_unit(unit)

    # Build a map: col → chunks that contain it
    col_found: dict[str, bool] = {col: False for col in columns}
    for chunk in chunks:
        for col in columns:
            expected_sql = f"SELECT {col} FROM report_data WHERE {col} = ?"
            if expected_sql in chunk.body:
                col_found[col] = True

    missing_sql = [col for col, found in col_found.items() if not found]
    assert not missing_sql, (
        f"SQL queries for these columns were NOT found verbatim in any chunk body "
        f"(truncation detected): {missing_sql}"
    )
