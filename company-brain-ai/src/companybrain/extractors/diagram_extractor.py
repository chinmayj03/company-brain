"""ADR-0061 E7 — vision sidecar for diagrams in docs/.

Scope is intentionally narrow: ``docs/**/*.png`` and ``docs/**/*.svg``. The
extractor reads the image, asks Claude with a vision-enabled prompt for a
compact JSON shape (title, description, components, edges), and emits a
``Diagram`` entity per file. The persistence step later wires ``REPRESENTS``
edges to ``DomainEntity`` rows (ADR-0059).

For SVGs we additionally fall back to a deterministic parse — pulling ``<text>``
labels — when no vision model is available, so the entity always carries at
least the labelled components.

The extractor follows the ADR-0057 ``Extractor`` protocol so dispatch and the
universal-extraction Stage 0.5b pick it up automatically once it is
registered.
"""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path
from typing import Optional

import structlog

from companybrain.extractors.base import Extractor
from companybrain.models.entities import (
    Diagram, DiagramComponent, DiagramEdge, ExtractedBatch,
)

log = structlog.get_logger(__name__)


_IMAGE_SUFFIXES = frozenset({".png", ".svg"})
# Bytes — anything bigger than this gets text-only fallback. Vision pricing
# is roughly ~$0.003 per ~1k tokens of image, so we cap input to keep cost
# bounded.
MAX_IMAGE_BYTES = 1_500_000
# The ADR scope: docs/ tree only. We keep this explicit so a top-level
# ``logo.png`` doesn't accidentally become a Diagram entity.
_DOCS_DIR_PARTS = ("docs", "doc")

_VISION_SYSTEM_PROMPT = """\
You are a software architecture analyst. Given a diagram image, return a
single compact JSON object describing it. No prose, no markdown.

Schema:
  {
    "title":       "<short noun phrase>",
    "description": "<one sentence on what the diagram depicts>",
    "components":  [{"name": "<label>", "role": "<service|database|queue|client|other>"}],
    "edges":       [{"source": "<label>", "target": "<label>", "label": "<verb phrase or empty>"}]
  }

Rules:
  - Use the exact labels visible on the diagram. Do not invent names.
  - 'role' must be one of the values listed in the schema. Use 'other' if unsure.
  - Skip purely decorative shapes (frames, swimlane backgrounds).
  - Return only the JSON object.
"""


class DiagramExtractor:
    kind = "diagram"

    def supports(self, path: Path) -> bool:
        if path.suffix.lower() not in _IMAGE_SUFFIXES:
            return False
        parts_lower = {p.lower() for p in path.parts}
        return any(d in parts_lower for d in _DOCS_DIR_PARTS)

    def extract(self, path: Path, content: str, *, repo: str = "") -> ExtractedBatch:
        # ``content`` is the text the dispatcher read with ``read_text`` —
        # garbage for binary PNGs but the SVG fallback uses it. We re-read
        # bytes when calling the vision API.
        diagram = self._extract_diagram(path, content, repo=repo)
        return ExtractedBatch(
            file=str(path),
            repo=repo,
            extractor_kind=self.kind,
            diagrams=[diagram] if diagram else [],
        )

    # ── internal ───────────────────────────────────────────────────────────

    def _extract_diagram(
        self, path: Path, content: str, *, repo: str,
    ) -> Optional[Diagram]:
        suffix = path.suffix.lower()
        # Try the vision API first when an Anthropic key is configured.
        if os.environ.get("ANTHROPIC_API_KEY") and self._is_safe_size(path):
            try:
                from_vision = _call_vision(path, suffix)
                if from_vision:
                    return self._merge(path, repo, from_vision)
            except Exception as e:
                log.debug("[diagram_extractor] vision call failed",
                          path=str(path), error=str(e))
        # Fallback: SVG text parse (we still get labels), or skip for PNG.
        if suffix == ".svg":
            data = _parse_svg(content) or {}
            if data:
                return self._merge(path, repo, data)
        # Last resort: emit an empty Diagram entity so cross-references work.
        return Diagram(
            repo=repo,
            file_path=str(path),
            title=path.stem,
            description="(diagram metadata unavailable — vision API not configured)",
            qualified_name=_make_qname(repo, path),
        )

    def _is_safe_size(self, path: Path) -> bool:
        try:
            return path.stat().st_size <= MAX_IMAGE_BYTES
        except OSError:
            return False

    def _merge(self, path: Path, repo: str, data: dict) -> Diagram:
        components = [
            DiagramComponent(name=str(c.get("name", "")).strip(),
                             role=(c.get("role") or None))
            for c in (data.get("components") or [])
            if c.get("name")
        ]
        edges = [
            DiagramEdge(source=str(e.get("source", "")).strip(),
                        target=str(e.get("target", "")).strip(),
                        label=(e.get("label") or None))
            for e in (data.get("edges") or [])
            if e.get("source") and e.get("target")
        ]
        return Diagram(
            repo=repo,
            file_path=str(path),
            title=str(data.get("title") or path.stem),
            description=str(data.get("description") or ""),
            components=components,
            edges=edges,
            qualified_name=_make_qname(repo, path),
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_qname(repo: str, path: Path) -> str:
    """Stable qualified name: ``<repo>.<rel-path-without-ext>``."""
    rel = str(path)
    rel = rel.rsplit(".", 1)[0]
    return f"{repo}.{rel}" if repo else rel


def _call_vision(path: Path, suffix: str) -> Optional[dict]:
    """Send the image to Claude with the vision-enabled chat surface.

    Uses the Anthropic SDK directly because the ``LLMProvider`` interface in
    this repo flattens ``content`` to a string. We keep the call self-
    contained so the diagram extractor still works on an OpenAI-only build —
    it will just take the SVG/PNG fallback path.
    """
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        return None
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    media_type = "image/png" if suffix == ".png" else "image/svg+xml"
    try:
        data_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    except OSError:
        return None
    model = os.environ.get("BRAIN_DIAGRAM_MODEL", "claude-sonnet-4-6")
    client = AsyncAnthropic(api_key=api_key)
    import asyncio

    async def _run() -> str:
        resp = await client.messages.create(
            model=model,
            max_tokens=1200,
            system=_VISION_SYSTEM_PROMPT,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image", "source": {
                        "type": "base64", "media_type": media_type, "data": data_b64,
                    }},
                    {"type": "text", "text":
                     "Analyse this diagram and return the JSON schema."},
                ],
            }],
        )
        return resp.content[0].text if resp.content else ""

    try:
        raw = _run_coroutine(_run())
    except Exception as e:
        log.debug("[diagram_extractor] async call failed", error=str(e))
        return None
    return _parse_vision_json(raw)


def _run_coroutine(coro):
    """Run a coroutine synchronously even if we're already inside an event
    loop. The extractor is called from the synchronous Stage-0.5b walk."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        import concurrent.futures
        # Run in a dedicated thread with its own event loop.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    return asyncio.run(coro)


def _parse_vision_json(raw: str) -> Optional[dict]:
    if not raw:
        return None
    text = raw.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    return data


_SVG_TEXT = re.compile(r"<text[^>]*>(.*?)</text>", re.IGNORECASE | re.DOTALL)
_SVG_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _parse_svg(content: str) -> Optional[dict]:
    """Deterministic SVG fallback: pull <title> and <text> labels."""
    if not content or "<svg" not in content.lower():
        return None
    title_match = _SVG_TITLE.search(content)
    title = (title_match.group(1).strip() if title_match else "") or ""
    components: list[dict] = []
    seen: set[str] = set()
    for m in _SVG_TEXT.finditer(content):
        label = re.sub(r"\s+", " ", m.group(1)).strip()
        # Strip nested SVG tags from the label.
        label = re.sub(r"<[^>]+>", "", label).strip()
        if not label or label in seen:
            continue
        seen.add(label)
        components.append({"name": label, "role": "other"})
    if not title and not components:
        return None
    return {
        "title": title or "diagram",
        "description": "(parsed from SVG <text> labels)",
        "components": components,
        "edges": [],
    }


# Register with dispatch (append-only — extends the ADR-0057 dispatcher).
def register_diagram_extractor() -> None:
    """Append the diagram extractor to the universal-extraction dispatch.

    Called once by the orchestrator import path; safe to call repeatedly —
    duplicates are filtered.
    """
    from companybrain.extractors import dispatch as _d
    if any(isinstance(e, DiagramExtractor) for e in _d._SCHEMA_EXTRACTORS):
        return
    _d.register_schema_extractor(DiagramExtractor())


# Auto-register on import so callers don't have to remember.
register_diagram_extractor()


__all__ = [
    "DiagramExtractor", "register_diagram_extractor",
    "MAX_IMAGE_BYTES",
]
