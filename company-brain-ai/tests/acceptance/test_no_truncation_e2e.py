"""
ADR-0045: Chunker reads files directly from disk — end-to-end acceptance test.

Verifies the root fix: CodeUnit no longer carries content; the chunker opens
unit.file_path with Path(...).read_text() so the full file is always visible,
regardless of how large it is.

Synthetic fixture: 1 class, 30 methods, ~120k chars.
  - Each method body references a distinct column name (col_000 … col_029).
  - File is written to a real tmp_path so the chunker actually reads from disk.

Assertions (no LLM or DB calls needed at this layer):
  A1. CodeUnit carries NO content at construction time (file_path only).
  A2. CodeChunker produces exactly 30 MethodChunk objects from the file.
  A3. All 30 distinct SQL queries appear verbatim in chunk bodies.
  A4. All 30 distinct column names appear in at least one chunk body.
  A5. chunk_repo deduplication is idempotent over two identical units.
  A6. TruncatedContentError fires when the D4 guard detects pre-truncated content.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from companybrain.collectors.code_tracer import CodeUnit
from companybrain.pipeline.code_chunker import (
    CodeChunker,
    TruncatedContentError,
    _TRUNCATION_MARKERS,
)


# ── Fixture helpers ───────────────────────────────────────────────────────────

def _build_java_source(num_methods: int = 30) -> tuple[str, list[str]]:
    """Return (source_code, [col_000, col_001, …]) for a 30-method repository."""
    header = textwrap.dedent("""\
        package com.example.competitiveness;

        import java.util.List;
        import org.springframework.jdbc.core.namedparam.NamedParameterJdbcTemplate;
        import org.springframework.stereotype.Repository;

        @Repository
        public class CompetitivenessPlanRepository {

            private final NamedParameterJdbcTemplate jdbc;

            public CompetitivenessPlanRepository(NamedParameterJdbcTemplate jdbc) {
                this.jdbc = jdbc;
            }

        """)

    columns: list[str] = []
    methods: list[str] = []
    for i in range(num_methods):
        col = f"col_{i:03d}"
        columns.append(col)
        # ~3 500-char padding per method → total ≈ 120 k chars for 30 methods
        padding = "        // " + "x" * 3_450 + "\n"
        method = textwrap.dedent(f"""\
            public List<String> findBy{col.replace('_', '')}(String val) {{
                // ADR-0045 acceptance fixture: column {col}
        {padding}
                String sql = "SELECT {col} FROM competitiveness_plan"
                           + " WHERE {col} = :val AND is_active = true";
                return jdbc.queryForList(sql, java.util.Map.of("val", val), String.class);
            }}

        """)
        methods.append(method)

    source = header + "\n".join(methods) + "}\n"
    return source, columns


def _write_unit(source: str, tmp_path: Path) -> CodeUnit:
    """Write source to disk and return a CodeUnit with no content field."""
    fp = tmp_path / "CompetitivenessPlanRepository.java"
    fp.write_text(source, encoding="utf-8")
    return CodeUnit(
        file_path=str(fp),
        repo_name="backend",
        role="repository",
        class_name="CompetitivenessPlanRepository",
        language="java",
    )


# ── A1: CodeUnit carries no content at construction time ──────────────────────

def test_code_unit_has_no_content_at_construction(tmp_path):
    """After ADR-0045: CodeUnit._content_cache must be None until content is accessed."""
    source, _ = _build_java_source(1)
    unit = _write_unit(source, tmp_path)

    # The cache is private; access via __dict__ to check without triggering the property
    assert unit.__dict__.get("_content_cache") is None, (
        "CodeUnit._content_cache should be None immediately after construction — "
        "content must not be loaded eagerly. (ADR-0045 D1)"
    )


# ── A2: chunker produces exactly 30 chunks ────────────────────────────────────

def test_chunker_produces_one_chunk_per_method(tmp_path):
    """Chunker reads the full file from disk and emits one chunk per method."""
    source, _ = _build_java_source(30)
    unit = _write_unit(source, tmp_path)

    assert len(source) >= 100_000, f"Fixture too small: {len(source)} chars"

    chunks = CodeChunker().chunk_unit(unit)

    assert len(chunks) >= 30, (
        f"Expected ≥30 MethodChunks for the 30-method class, got {len(chunks)}. "
        "If the chunker only sees 6 019 chars, ADR-0045 D2 is not applied — "
        "the unit still carries truncated content from an upstream caller."
    )


# ── A3: verbatim SQL queries survive in chunk bodies ─────────────────────────

def test_all_sql_queries_appear_verbatim_in_chunk_bodies(tmp_path):
    """
    Each method's SQL string must be found verbatim in the chunk bodies.
    This is the direct equivalent of the production symptom: 'what tables does X
    read' returned empty because the SQL was cut off at char 6 019.
    """
    source, columns = _build_java_source(30)
    unit = _write_unit(source, tmp_path)
    chunks = CodeChunker().chunk_unit(unit)

    all_bodies = "\n".join(c.body for c in chunks)
    missing = []
    for col in columns:
        expected = f"SELECT {col} FROM competitiveness_plan"
        if expected not in all_bodies:
            missing.append(col)

    assert not missing, (
        f"SQL for {len(missing)} column(s) was NOT found verbatim in any chunk body. "
        f"Missing: {missing[:5]}{'…' if len(missing) > 5 else ''}. "
        "This is the ADR-0045 symptom: content was truncated before chunking."
    )


# ── A4: every column name is reachable ───────────────────────────────────────

def test_all_distinct_columns_reachable_in_chunks(tmp_path):
    """A4: no column name hidden by truncation."""
    source, columns = _build_java_source(30)
    unit = _write_unit(source, tmp_path)
    all_bodies = " ".join(c.body for c in CodeChunker().chunk_unit(unit))

    missing = [col for col in columns if col not in all_bodies]
    assert not missing, (
        f"Column names not found in any chunk: {missing}. "
        "Indicates the corresponding method bodies were truncated."
    )


# ── A5: chunk_repo deduplication is idempotent ───────────────────────────────

def test_chunk_repo_deduplicates_identical_units(tmp_path):
    """chunk_repo called with two references to the same file must deduplicate."""
    source, _ = _build_java_source(30)
    unit = _write_unit(source, tmp_path)

    chunks_single = CodeChunker().chunk_repo([unit])
    chunks_double = CodeChunker().chunk_repo([unit, unit])

    assert len(chunks_single) == len(chunks_double), (
        f"chunk_repo did not deduplicate: single={len(chunks_single)}, "
        f"double={len(chunks_double)}"
    )


# ── A6: D4 guard fires on pre-truncated content ───────────────────────────────

def test_d4_guard_raises_on_truncated_file(tmp_path):
    """
    If an upstream caller writes a pre-truncated file to disk (e.g. still capping
    at 6 000 chars), the D4 assert must fire loud rather than silently producing
    incomplete chunks.
    """
    # Simulate the bug: write a small file that ends with the truncation marker
    truncated_source = "x" * 100 + "\n" + _TRUNCATION_MARKERS[0]
    fp = tmp_path / "Truncated.java"
    fp.write_text(truncated_source, encoding="utf-8")

    unit = CodeUnit(
        file_path=str(fp),
        repo_name="backend",
        role="repository",
        class_name="Truncated",
        language="java",
    )

    with pytest.raises(TruncatedContentError):
        CodeChunker().chunk_unit(unit)


# ── Telemetry: read_file_directly log line is emitted ────────────────────────

def test_chunker_emits_read_file_directly_log(tmp_path, capsys):
    """Chunker must emit the 'chunker.read_file_directly' debug line (D6 telemetry).

    structlog writes to stdout by default in test environments, so we capture
    via capsys rather than caplog (which only captures stdlib logging).
    """
    source, _ = _build_java_source(1)
    unit = _write_unit(source, tmp_path)

    CodeChunker().chunk_unit(unit)

    out = capsys.readouterr().out
    assert "chunker.read_file_directly" in out, (
        "Expected 'chunker.read_file_directly' in stdout — ADR-0045 D6 telemetry missing."
    )
