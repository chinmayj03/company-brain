"""Unit tests for the diagram image extractor (ADR-0052 P6).

The vision call goes to the LLM provider's underlying client. Tests
monkeypatch the provider so we never hit Anthropic in CI; we just verify
the parser, the artifact shape, and the unsupported-format short-circuit.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from companybrain.harness import image_extractor as ie


# ── parser ───────────────────────────────────────────────────────────────────

def test_parse_strips_json_fence():
    """The LLM sometimes wraps JSON in ```json ... ``` — we tolerate both."""
    raw = '```json\n{"components": [], "edges": []}\n```'
    parsed = ie._parse(raw)
    assert parsed is not None
    assert parsed.components == []
    assert parsed.edges == []


def test_parse_returns_none_for_invalid_json():
    assert ie._parse("not json at all") is None


def test_parse_drops_non_dict_entries():
    """Garbage entries inside the lists are filtered out, not crashed-on."""
    raw = '{"components": [{"name": "A", "kind": "service"}, "junk"], "edges": []}'
    parsed = ie._parse(raw)
    assert parsed is not None
    assert parsed.components == [{"name": "A", "kind": "service"}]


# ── format gate ──────────────────────────────────────────────────────────────

def test_is_supported_image_for_known_extensions():
    assert ie.is_supported_image(Path("a.png"))
    assert ie.is_supported_image(Path("b.JPG"))
    assert ie.is_supported_image(Path("c.gif"))


def test_is_supported_image_rejects_unknown():
    assert not ie.is_supported_image(Path("readme.txt"))
    assert not ie.is_supported_image(Path("schema.dot"))


# ── extract_diagram ──────────────────────────────────────────────────────────

class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessages:
    def __init__(self, response_text: str) -> None:
        self._text = response_text

    async def create(self, **kwargs: Any) -> Any:
        msg = type("Msg", (), {})()
        msg.content = [_FakeBlock(self._text)]
        return msg


class _FakeClient:
    def __init__(self, response_text: str) -> None:
        self.messages = _FakeMessages(response_text)


class _FakeProvider:
    provider_name = "fake"

    def __init__(self, response_text: str) -> None:
        self._client = _FakeClient(response_text)

    def model_for_role(self, role: Any) -> str:
        return "fake-model"


@pytest.mark.asyncio
async def test_extract_diagram_returns_artifact(monkeypatch, tmp_path: Path):
    """A vision-capable provider produces a diagram Artifact with parsed counts."""
    img = tmp_path / "arch.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\nstub")    # bytes don't matter, only base64.

    response = (
        '{"components": [{"name": "Auth", "kind": "service"}, '
        '{"name": "DB", "kind": "database"}], '
        '"edges": [{"from": "Auth", "to": "DB", "label": "writes"}]}'
    )
    monkeypatch.setattr(ie, "get_provider", lambda: _FakeProvider(response))

    artifact = await ie.extract_diagram(img)

    assert artifact is not None
    assert artifact.kind == "diagram"
    assert artifact.metadata["components_count"] == 2
    assert artifact.metadata["edges_count"] == 1
    assert artifact.external_id == str(img)


@pytest.mark.asyncio
async def test_extract_diagram_returns_none_for_missing_file(tmp_path: Path):
    assert await ie.extract_diagram(tmp_path / "nope.png") is None


@pytest.mark.asyncio
async def test_extract_diagram_returns_none_for_unsupported_format(
    monkeypatch, tmp_path: Path,
):
    f = tmp_path / "schema.dot"
    f.write_text("digraph G {}")
    # Provider must NOT be called for an unsupported format.
    monkeypatch.setattr(ie, "get_provider",
                        lambda: pytest.fail("provider must not be called"))
    assert await ie.extract_diagram(f) is None


@pytest.mark.asyncio
async def test_extract_diagram_handles_provider_without_vision(
    monkeypatch, tmp_path: Path,
):
    """Ollama and other text-only providers should return None, not error."""
    img = tmp_path / "arch.png"
    img.write_bytes(b"stub")

    class _NoClientProvider:
        provider_name = "ollama"
        _client = None

        def model_for_role(self, role: Any) -> str:
            return "qwen2.5"

    monkeypatch.setattr(ie, "get_provider", lambda: _NoClientProvider())

    assert await ie.extract_diagram(img) is None


@pytest.mark.asyncio
async def test_extract_repo_diagrams_walks_docs(monkeypatch, tmp_path: Path):
    """``extract_repo_diagrams`` only scans ``docs/`` and returns one Artifact per image."""
    docs = tmp_path / "docs" / "arch"
    docs.mkdir(parents=True)
    (docs / "diagram.png").write_bytes(b"stub")
    # A PNG outside docs/ should be ignored.
    (tmp_path / "stray.png").write_bytes(b"stub")

    monkeypatch.setattr(ie, "get_provider",
                        lambda: _FakeProvider('{"components": [], "edges": []}'))

    artifacts = await ie.extract_repo_diagrams(tmp_path)
    assert len(artifacts) == 1
    assert artifacts[0].external_id == str(docs / "diagram.png")
