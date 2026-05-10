"""Unit tests for pipeline/structural_prepass.py (ADR-0011)."""
import os
from pathlib import Path
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from companybrain.pipeline.structural_prepass import run_structural_prepass, _local_structural_hash
from companybrain.collectors.code_tracer import FocalContext, CodeUnit

# Tests use repo_path="/tmp/pilot"; files must exist there so the lazy
# content property can read them. ADR-0045: CodeUnit.content reads from disk.
_REPO_ROOT = "/tmp/pilot"


def _make_unit(file_path: str, content: str, language: str = "java") -> CodeUnit:
    """Write content to {_REPO_ROOT}/{file_path} and return a CodeUnit with absolute path."""
    abs_path = os.path.join(_REPO_ROOT, file_path)
    os.makedirs(os.path.dirname(abs_path), exist_ok=True)
    Path(abs_path).write_text(content, encoding="utf-8")
    return CodeUnit(
        file_path=abs_path,
        repo_name="pilot",
        role="service",
        class_name=file_path.split("/")[-1].replace(".java", "").replace(".py", ""),
        language=language,
    )


def _make_focal(units: list[CodeUnit]) -> FocalContext:
    fc = FocalContext(endpoint="/api/users", method="GET")
    fc.code_units = units
    return fc


def _mock_response(status: int, body: dict):
    """Build a minimal mock that satisfies httpx.AsyncClient.post/get usage."""
    r = MagicMock()
    r.status_code = status
    r.raise_for_status = MagicMock()  # no-op
    r.json = MagicMock(return_value=body)
    return r


async def test_prepass_marks_unchanged_files_fresh():
    content = "public class Foo { void bar() {} }"
    unit = _make_unit("src/Foo.java", content)
    fc = _make_focal([unit])
    fake_hash = _local_structural_hash(content)

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=_mock_response(200, {}))
    mock_client.get = AsyncMock(return_value=_mock_response(200, {
        "fingerprints": [
            {
                "file_path": "src/Foo.java",
                "structural_hash": fake_hash,
                "function_count": 1,
                "class_count": 1,
            }
        ]
    }))

    with patch("companybrain.pipeline.structural_prepass.httpx.AsyncClient", return_value=mock_client):
        result = await run_structural_prepass(
            repo_path="/tmp/pilot",
            commit_sha="abc123",
            workspace_id="ws-1",
            focal_context=fc,
        )

    assert len(result.fresh_units) == 1
    assert len(result.dirty_units) == 0
    assert result.cb_api_status == "ok"


async def test_prepass_marks_changed_files_dirty():
    content = "public class Foo { void bar() {} }"
    unit = _make_unit("src/Foo.java", content)
    fc = _make_focal([unit])

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=_mock_response(200, {}))
    mock_client.get = AsyncMock(return_value=_mock_response(200, {
        "fingerprints": [
            {
                "file_path": "src/Foo.java",
                "structural_hash": "old-hash-that-does-not-match",
                "function_count": 0,
                "class_count": 1,
            }
        ]
    }))

    with patch("companybrain.pipeline.structural_prepass.httpx.AsyncClient", return_value=mock_client):
        result = await run_structural_prepass(
            repo_path="/tmp/pilot",
            commit_sha="abc123",
            workspace_id="ws-1",
            focal_context=fc,
        )

    assert len(result.fresh_units) == 0
    assert len(result.dirty_units) == 1
    assert result.cb_api_status == "ok"


async def test_prepass_falls_back_to_dirty_when_cb_api_extract_fails():
    content = "public class Foo {}"
    unit = _make_unit("src/Foo.java", content)
    fc = _make_focal([unit])

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(side_effect=Exception("ECONNREFUSED"))

    with patch("companybrain.pipeline.structural_prepass.httpx.AsyncClient", return_value=mock_client):
        result = await run_structural_prepass(
            repo_path="/tmp/pilot",
            commit_sha="abc",
            workspace_id="ws",
            focal_context=fc,
        )

    assert len(result.dirty_units) == 1
    assert result.fresh_units == []
    assert result.cb_api_status.startswith("failed:")


async def test_prepass_falls_back_to_dirty_when_fingerprints_fails():
    content = "public class Foo {}"
    unit = _make_unit("src/Foo.java", content)
    fc = _make_focal([unit])

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=_mock_response(200, {}))
    mock_client.get = AsyncMock(side_effect=Exception("timeout"))

    with patch("companybrain.pipeline.structural_prepass.httpx.AsyncClient", return_value=mock_client):
        result = await run_structural_prepass(
            repo_path="/tmp/pilot",
            commit_sha="abc",
            workspace_id="ws",
            focal_context=fc,
        )

    assert len(result.dirty_units) == 1
    assert result.fresh_units == []
    assert result.cb_api_status.startswith("failed:")


async def test_prepass_handles_empty_focal_context():
    fc = _make_focal([])

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=_mock_response(200, {}))
    mock_client.get = AsyncMock(return_value=_mock_response(200, {"fingerprints": []}))

    with patch("companybrain.pipeline.structural_prepass.httpx.AsyncClient", return_value=mock_client):
        result = await run_structural_prepass(
            repo_path="/tmp/pilot",
            commit_sha="abc",
            workspace_id="ws",
            focal_context=fc,
        )

    assert result.fresh_units == []
    assert result.dirty_units == []
    assert result.cb_api_status == "ok"


def test_local_structural_hash_stable_across_whitespace():
    code_a = "public class Foo {\n    void bar() {}\n}"
    code_b = "public class Foo {\n\n    void bar() {}\n\n}"
    assert _local_structural_hash(code_a) == _local_structural_hash(code_b)


def test_local_structural_hash_changes_on_rename():
    # The heuristic captures top-level class/def names via regex, so a class
    # rename is detected while an inline method rename (same line, no modifier)
    # is not — that refinement belongs to the tree-sitter follow-up ADR.
    code_a = "public class Foo { void bar() {} }"
    code_b = "public class Bar { void bar() {} }"  # class renamed Foo → Bar
    assert _local_structural_hash(code_a) != _local_structural_hash(code_b)


def test_local_structural_hash_python():
    code = "def foo():\n    pass\n\nclass Bar:\n    def baz(self): pass\n"
    h = _local_structural_hash(code)
    assert isinstance(h, str) and len(h) == 64  # sha256 hex
