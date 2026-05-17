"""
Acceptance test — A1.1 SQL Deep Extractor.

Golden-set coverage gate: > 75% of the 30 fixture files must yield at least
one extracted SQL statement.

Additional assertions:
  - All DML types (SELECT / INSERT / UPDATE / DELETE) are found across the set.
  - JPA @Query strings are found in >= 5 Java fixture files.
  - Confidence tiers are correctly assigned (verified per fixture category).
  - No regression: SchemaSqlExtractor still works when SQL_DEEP_EXTRACTOR_ENABLED=false.
  - Feature flag: when off, SqlDeepExtractor delegates to legacy extractor.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

# ── paths ──────────────────────────────────────────────────────────────────────

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "sql_golden_set"
assert FIXTURES_DIR.exists(), f"Golden-set directory not found: {FIXTURES_DIR}"

# ── helpers ────────────────────────────────────────────────────────────────────


def _load_fixture(name: str) -> tuple[Path, str]:
    path = FIXTURES_DIR / name
    assert path.exists(), f"Fixture not found: {path}"
    return path, path.read_text(encoding="utf-8")


def _scan_java(path: Path, content: str, *, repo: str = "test") -> list:
    """Run SqlEmbeddedScanner on a Java file and return the statements list."""
    from companybrain.extractors.sql_embedded_scanner import SqlEmbeddedScanner
    scanner = SqlEmbeddedScanner()
    assert scanner.supports(path), f"{path} should be supported by SqlEmbeddedScanner"
    batch = scanner.scan(path, content, repo=repo)
    return batch.statements


def _scan_sql(path: Path, content: str, *, repo: str = "test") -> list:
    """Run SqlDeepExtractor on a .sql file and return the statements list."""
    from companybrain.extractors.sql_deep import SqlDeepExtractor
    extractor = SqlDeepExtractor()
    assert extractor.supports(path)
    result = extractor.extract(path, content, repo=repo)
    deep_batch = getattr(result, "_sql_deep_batch", None)
    assert deep_batch is not None, "Expected _sql_deep_batch attribute on result"
    return deep_batch.statements


# ── individual fixture tests ───────────────────────────────────────────────────

class TestJpaAnnotations:
    """Fixtures 01–10: JPA @Query patterns."""

    JPA_FIXTURES = [
        "01_jpa_select_by_id.java",
        "02_jpa_select_by_workspace_kind.java",
        "03_jpa_search_by_name.java",
        "04_jpa_multiline_select.java",
        "05_jpa_native_update.java",
        "06_jpa_native_multiline_update.java",
        "07_jpa_modifying_update.java",
        "08_jpa_prune_edges.java",
        "09_jpa_dirty_nodes.java",
        "10_jpa_mark_consumed.java",
    ]

    @pytest.mark.parametrize("fixture_name", JPA_FIXTURES)
    def test_jpa_fixture_yields_at_least_one_statement(self, fixture_name: str):
        path, content = _load_fixture(fixture_name)
        stmts = _scan_java(path, content)
        assert stmts, f"{fixture_name}: expected ≥1 SQL statement, got 0"

    def test_jpa_fixtures_have_prepared_statement_tier(self):
        """JPA @Query with :param should be assigned prepared_statement tier."""
        from companybrain.extractors.sql_deep import TIER_PREPARED_STATEMENT
        hits = 0
        for fixture_name in self.JPA_FIXTURES:
            path, content = _load_fixture(fixture_name)
            stmts = _scan_java(path, content)
            if any(s.confidence_tier == TIER_PREPARED_STATEMENT for s in stmts):
                hits += 1
        assert hits >= 5, f"Expected ≥5 JPA fixtures with prepared_statement tier, got {hits}"

    def test_jpa_update_statements_extracted(self):
        """UPDATE statements in @Modifying @Query must be extracted."""
        update_files = [
            "05_jpa_native_update.java",
            "06_jpa_native_multiline_update.java",
            "07_jpa_modifying_update.java",
            "08_jpa_prune_edges.java",
        ]
        for fixture_name in update_files:
            path, content = _load_fixture(fixture_name)
            stmts = _scan_java(path, content)
            assert stmts, f"{fixture_name}: expected ≥1 statement"


class TestMyBatisAnnotations:
    """Fixtures 11–15: MyBatis @Select / @Insert / @Update / @Delete."""

    MYBATIS_FIXTURES = {
        "11_mybatis_select.java": "SELECT",
        "12_mybatis_insert.java": "INSERT",
        "13_mybatis_update.java": "UPDATE",
        "14_mybatis_delete.java": "DELETE",
        "15_mybatis_array_form.java": "SELECT",
    }

    @pytest.mark.parametrize("fixture_name,expected_type", MYBATIS_FIXTURES.items())
    def test_mybatis_fixture_extracts_correct_type(self, fixture_name: str, expected_type: str):
        path, content = _load_fixture(fixture_name)
        stmts = _scan_java(path, content)
        assert stmts, f"{fixture_name}: expected ≥1 statement"
        stmt_types = {s.stmt_type for s in stmts}
        assert expected_type in stmt_types, (
            f"{fixture_name}: expected stmt_type {expected_type!r}, got {stmt_types}"
        )


class TestJdbcPatterns:
    """Fixtures 16–20: JDBC PreparedStatement / JdbcTemplate patterns."""

    JDBC_FIXTURES = [
        "16_jdbc_prepare_statement.java",
        "17_jdbc_execute_update.java",
        "18_jdbc_template_query.java",
        "19_jdbc_template_update.java",
        "20_jdbc_execute_query.java",
    ]

    @pytest.mark.parametrize("fixture_name", JDBC_FIXTURES)
    def test_jdbc_fixture_yields_at_least_one_statement(self, fixture_name: str):
        path, content = _load_fixture(fixture_name)
        stmts = _scan_java(path, content)
        assert stmts, f"{fixture_name}: expected ≥1 SQL statement, got 0"

    def test_jdbc_prepared_statements_have_correct_tier(self):
        """JDBC PreparedStatement fixtures with ? must have prepared_statement tier."""
        from companybrain.extractors.sql_deep import TIER_PREPARED_STATEMENT
        for fixture_name in self.JDBC_FIXTURES:
            path, content = _load_fixture(fixture_name)
            stmts = _scan_java(path, content)
            # All JDBC fixtures have ? placeholders.
            assert any(s.confidence_tier == TIER_PREPARED_STATEMENT for s in stmts), (
                f"{fixture_name}: expected prepared_statement tier"
            )

    def test_dynamic_concat_tier_detected(self):
        """Fixture 30 (dynamic concat) should be detected as dynamic_concat tier."""
        from companybrain.extractors.sql_deep import TIER_DYNAMIC_CONCAT
        path, content = _load_fixture("30_jdbc_dynamic_concat.java")
        stmts = _scan_java(path, content)
        assert stmts, "30_jdbc_dynamic_concat.java: expected ≥1 statement"
        # The scanner's JDBC pattern sees a prepareStatement call + concat after string.
        assert any(s.confidence_tier == TIER_DYNAMIC_CONCAT for s in stmts), (
            f"Expected dynamic_concat tier, got: {[s.confidence_tier for s in stmts]}"
        )


class TestRawSqlFiles:
    """Fixtures 21–28: raw .sql files with DDL and DML."""

    DDL_FIXTURES = [
        "21_ddl_create_table.sql",
        "22_ddl_alter_table.sql",
        "23_ddl_create_index.sql",
    ]
    DML_FIXTURES = [
        "24_dml_insert.sql",
        "25_dml_update.sql",
        "26_dml_delete.sql",
        "27_dql_select_complex.sql",
        "28_dql_select_join.sql",
    ]

    @pytest.mark.parametrize("fixture_name", DDL_FIXTURES + DML_FIXTURES)
    def test_sql_file_yields_statements(self, fixture_name: str):
        path, content = _load_fixture(fixture_name)
        stmts = _scan_sql(path, content)
        assert stmts, f"{fixture_name}: expected ≥1 statement"

    def test_all_dml_types_present(self):
        """SELECT, INSERT, UPDATE, DELETE must all appear in DML fixtures."""
        from companybrain.extractors.sql_deep import SqlDeepExtractor
        extractor = SqlDeepExtractor()
        found_types: set[str] = set()
        for fixture_name in self.DML_FIXTURES:
            path, content = _load_fixture(fixture_name)
            result = extractor.extract(path, content, repo="test")
            deep = getattr(result, "_sql_deep_batch", None)
            if deep:
                for s in deep.statements:
                    found_types.add(s.stmt_type)
        for required in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert required in found_types, (
                f"DML type {required!r} not found in any DML fixture. Found: {found_types}"
            )

    def test_select_has_tables_extracted(self):
        """SELECT fixtures should have tables list populated."""
        for fixture_name in ("27_dql_select_complex.sql", "28_dql_select_join.sql"):
            path, content = _load_fixture(fixture_name)
            stmts = _scan_sql(path, content)
            select_stmts = [s for s in stmts if s.stmt_type == "SELECT"]
            assert select_stmts, f"{fixture_name}: expected SELECT statements"
            # At least one SELECT must have tables extracted.
            assert any(s.tables for s in select_stmts), (
                f"{fixture_name}: expected tables extracted from SELECT"
            )


class TestEntityManager:
    """Fixture 29: entityManager.createQuery."""

    def test_entity_manager_create_query_extracted(self):
        path, content = _load_fixture("29_entitymanager_createquery.java")
        stmts = _scan_java(path, content)
        assert stmts, "29_entitymanager_createquery.java: expected ≥1 statement"

    def test_entity_manager_native_query_extracted(self):
        """createNativeQuery with GROUP BY should be extracted."""
        path, content = _load_fixture("29_entitymanager_createquery.java")
        stmts = _scan_java(path, content)
        stmt_types = {s.stmt_type for s in stmts}
        assert "SELECT" in stmt_types, f"Expected SELECT, got: {stmt_types}"


# ── golden-set gate ────────────────────────────────────────────────────────────

class TestGoldenSetCoverage:
    """Master coverage gate: > 75% of all 30 fixtures must yield ≥1 statement."""

    ALL_FIXTURES = sorted(FIXTURES_DIR.glob("*.java")) + sorted(FIXTURES_DIR.glob("*.sql"))

    def test_coverage_above_75_percent(self):
        from companybrain.extractors.sql_deep import SqlDeepExtractor
        from companybrain.extractors.sql_embedded_scanner import SqlEmbeddedScanner

        extractor = SqlDeepExtractor()
        scanner = SqlEmbeddedScanner()

        covered = 0
        total = len(self.ALL_FIXTURES)
        missing: list[str] = []

        for path in self.ALL_FIXTURES:
            content = path.read_text(encoding="utf-8")
            if path.suffix == ".java":
                stmts = scanner.scan(path, content, repo="test").statements
            else:
                result = extractor.extract(path, content, repo="test")
                deep = getattr(result, "_sql_deep_batch", None)
                stmts = deep.statements if deep else []

            if stmts:
                covered += 1
            else:
                missing.append(path.name)

        pct = covered / total * 100
        assert pct > 75, (
            f"Golden-set coverage {pct:.1f}% is below 75% threshold. "
            f"Missing fixtures ({len(missing)}): {missing}"
        )

    def test_jpa_found_in_at_least_5_files(self):
        """JPA @Query strings must be found in ≥5 Java fixture files."""
        from companybrain.extractors.sql_embedded_scanner import SqlEmbeddedScanner
        scanner = SqlEmbeddedScanner()

        jpa_files_hit = 0
        for path in FIXTURES_DIR.glob("*_jpa_*.java"):
            content = path.read_text(encoding="utf-8")
            stmts = scanner.scan(path, content, repo="test").statements
            if stmts:
                jpa_files_hit += 1

        assert jpa_files_hit >= 5, (
            f"JPA @Query found in only {jpa_files_hit} files; need ≥5"
        )

    def test_all_dml_types_in_golden_set(self):
        """SELECT, INSERT, UPDATE, DELETE must all appear across the 30 fixtures."""
        from companybrain.extractors.sql_deep import SqlDeepExtractor
        from companybrain.extractors.sql_embedded_scanner import SqlEmbeddedScanner

        extractor = SqlDeepExtractor()
        scanner = SqlEmbeddedScanner()
        found_types: set[str] = set()

        for path in self.ALL_FIXTURES:
            content = path.read_text(encoding="utf-8")
            if path.suffix == ".java":
                stmts = scanner.scan(path, content, repo="test").statements
            else:
                result = extractor.extract(path, content, repo="test")
                deep = getattr(result, "_sql_deep_batch", None)
                stmts = deep.statements if deep else []
            for s in stmts:
                found_types.add(s.stmt_type)

        for required in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            assert required in found_types, (
                f"DML type {required!r} missing from golden set. Found: {found_types}"
            )


# ── feature flag + legacy regression ──────────────────────────────────────────

class TestFeatureFlag:
    """Verify the SQL_DEEP_EXTRACTOR_ENABLED flag routes correctly."""

    def test_flag_off_delegates_to_legacy(self, monkeypatch):
        """When flag=false, SqlDeepExtractor returns legacy DDL extraction."""
        from pathlib import Path
        from companybrain.extractors.sql_deep import SqlDeepExtractor

        monkeypatch.setenv("SQL_DEEP_EXTRACTOR_ENABLED", "false")
        extractor = SqlDeepExtractor()
        path, content = _load_fixture("21_ddl_create_table.sql")
        result = extractor.extract(path, content, repo="test")
        # Legacy result has no _sql_deep_batch
        assert not hasattr(result, "_sql_deep_batch"), (
            "With flag=false, _sql_deep_batch should not be present"
        )
        # But legacy DDL extraction should still work.
        legacy_batch = getattr(result, "_schema_batch", None)
        assert legacy_batch is not None, "Legacy schema batch missing"
        assert legacy_batch.tables, "Legacy DDL extraction should find tables"

    def test_flag_on_returns_deep_batch(self, monkeypatch):
        """When flag=true (default), _sql_deep_batch is attached."""
        from companybrain.extractors.sql_deep import SqlDeepExtractor
        monkeypatch.setenv("SQL_DEEP_EXTRACTOR_ENABLED", "true")
        extractor = SqlDeepExtractor()
        path, content = _load_fixture("24_dml_insert.sql")
        result = extractor.extract(path, content, repo="test")
        assert hasattr(result, "_sql_deep_batch"), "_sql_deep_batch missing with flag=true"
        deep = result._sql_deep_batch
        assert deep.statements, "Expected statements from INSERT fixture"

    def test_legacy_schema_extractor_unaffected(self):
        """SchemaSqlExtractor must work independently of the feature flag."""
        from companybrain.extractors.schema_sql import SchemaSqlExtractor
        extractor = SchemaSqlExtractor()
        path, content = _load_fixture("21_ddl_create_table.sql")
        result = extractor.extract(path, content, repo="test")
        batch = result._schema_batch
        assert batch.tables, "Legacy SchemaSqlExtractor must still extract tables"
        table_names = {t.name for t in batch.tables}
        assert "workspaces" in table_names
        assert "workspace_sources" in table_names


# ── confidence tier assignment ─────────────────────────────────────────────────

class TestConfidenceTiers:
    """Tier assignment must be correct per fixture category."""

    def test_raw_sql_files_are_literal_string(self):
        """Raw .sql files have no placeholders → literal_string tier."""
        from companybrain.extractors.sql_deep import SqlDeepExtractor, TIER_LITERAL_STRING
        extractor = SqlDeepExtractor()
        for fixture_name in ("25_dml_update.sql", "27_dql_select_complex.sql"):
            path, content = _load_fixture(fixture_name)
            result = extractor.extract(path, content, repo="test")
            deep = result._sql_deep_batch
            assert all(s.confidence_tier == TIER_LITERAL_STRING for s in deep.statements), (
                f"{fixture_name}: expected all statements to have literal_string tier, "
                f"got: {[s.confidence_tier for s in deep.statements]}"
            )

    def test_jpa_param_queries_are_prepared_statement(self):
        """@Query with :param → prepared_statement."""
        from companybrain.extractors.sql_embedded_scanner import SqlEmbeddedScanner
        from companybrain.extractors.sql_deep import TIER_PREPARED_STATEMENT
        scanner = SqlEmbeddedScanner()
        path, content = _load_fixture("01_jpa_select_by_id.java")
        stmts = scanner.scan(path, content, repo="test").statements
        assert any(s.confidence_tier == TIER_PREPARED_STATEMENT for s in stmts), (
            f"Expected prepared_statement for @Query with :ids, got: {[s.confidence_tier for s in stmts]}"
        )

    def test_mybatis_param_queries_are_prepared_statement(self):
        """@Select with #{param} → prepared_statement."""
        from companybrain.extractors.sql_embedded_scanner import SqlEmbeddedScanner
        from companybrain.extractors.sql_deep import TIER_PREPARED_STATEMENT
        scanner = SqlEmbeddedScanner()
        path, content = _load_fixture("11_mybatis_select.java")
        stmts = scanner.scan(path, content, repo="test").statements
        assert any(s.confidence_tier == TIER_PREPARED_STATEMENT for s in stmts)
