"""Vision-extract architecture diagrams from images (ADR-0052 P6).

Architecture diagrams are an underused source of ground truth — they are
authored by the same engineers writing the code, embed business intent
("auth → billing → ledger"), and rot less aggressively than READMEs because
they live inside design docs that get reviewed.

This module asks a vision-capable LLM to convert ``docs/**/*.{png,svg,jpg}``
into structured ``{components, edges}`` JSON, then wraps the result in an
:class:`Artifact` of kind ``"diagram"``. The extractor is a side pass — the
pipeline orchestrator runs it after ``brain index`` finishes and before the
context-synthesis stage so diagram nodes can flow through the same merge logic
as code-derived nodes.

The implementation is provider-agnostic. We try the LLM provider's vision
path first; if the configured provider doesn't support image input the
extractor logs and returns ``None`` so the pipeline continues without
diagrams instead of failing.
"""
from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import structlog

from companybrain.llm import ChatMessage, TaskRole, get_provider
from companybrain.models.entities import Artifact

log = structlog.get_logger(__name__)


_PROMPT = """\
Analyse this architecture diagram. Identify each labelled box/component and
any directed edges between them. Return strictly valid JSON with this shape:

{
  "components": [{"name": "...", "kind": "service|database|queue|external|other"}],
  "edges":      [{"from": "...", "to": "...", "label": "..."}]
}

Rules:
- Use the human-readable label written inside each box for `name`.
- If you can't tell what kind a component is, use "other".
- Drop edges whose direction is unclear rather than guessing.
- Return ONLY the JSON. No prose, no markdown fences."""


_IMAGE_MEDIA_TYPES = {
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
    ".webp": "image/webp",
    ".svg":  "image/svg+xml",
}


@dataclass
class DiagramExtraction:
    """Parsed components + edges. ``raw`` is what the LLM actually returned."""
    components: list[dict[str, str]]
    edges: list[dict[str, str]]
    raw: str

    def as_dict(self) -> dict[str, Any]:
        return {"components": self.components, "edges": self.edges}


def is_supported_image(path: Path) -> bool:
    """True for the formats Anthropic's vision API accepts."""
    return path.suffix.lower() in _IMAGE_MEDIA_TYPES


async def extract_diagram(image_path: Path | str) -> Optional[Artifact]:
    """Run vision extraction. Returns ``None`` when the provider can't see images.

    Failures (parse errors, provider unsupported) downgrade to ``None`` rather
    than raising — diagram extraction is best-effort enrichment.
    """
    path = Path(image_path)
    if not path.is_file():
        log.warning("image_extractor.missing", path=str(path))
        return None
    if not is_supported_image(path):
        log.debug("image_extractor.unsupported", path=str(path))
        return None

    media_type = _IMAGE_MEDIA_TYPES[path.suffix.lower()]
    try:
        image_b64 = base64.b64encode(path.read_bytes()).decode()
    except OSError as exc:
        log.warning("image_extractor.read_failed", path=str(path), error=str(exc))
        return None

    raw = await _call_vision_llm(image_b64, media_type)
    if not raw:
        return None

    parsed = _parse(raw)
    if parsed is None:
        log.warning("image_extractor.parse_failed", path=str(path),
                    preview=raw[:200])
        return None

    return Artifact(
        kind="diagram",
        external_id=str(path),
        content=json.dumps(parsed.as_dict()),
        source_uri=str(path),
        metadata={
            "source_image": str(path),
            "components_count": len(parsed.components),
            "edges_count": len(parsed.edges),
        },
    )


# ── provider-specific call ───────────────────────────────────────────────────

async def _call_vision_llm(image_b64: str, media_type: str) -> str:
    """Best-effort vision invocation against the configured provider.

    Anthropic accepts multimodal content blocks via the raw client; our
    abstract :class:`LLMProvider.chat` only takes string content, so we drop
    to the underlying client when it's an Anthropic provider and skip
    otherwise.
    """
    provider = get_provider()
    client = getattr(provider, "_client", None)
    model = provider.model_for_role(TaskRole.BALANCED)

    if client is None or not hasattr(client, "messages"):
        log.info("image_extractor.no_vision_support",
                 provider=provider.provider_name)
        return ""

    try:
        msg = await client.messages.create(
            model=model,
            max_tokens=2_000,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": image_b64,
                        },
                    },
                    {"type": "text", "text": _PROMPT},
                ],
            }],
        )
    except Exception as exc:                       # pragma: no cover — provider-dep
        log.warning("image_extractor.llm_failed", error=str(exc))
        return ""

    blocks = getattr(msg, "content", []) or []
    text_parts: list[str] = []
    for block in blocks:
        text = getattr(block, "text", None)
        if text:
            text_parts.append(text)
    return "".join(text_parts)


def _parse(raw: str) -> Optional[DiagramExtraction]:
    """Tolerant JSON parser — strips fences, validates the schema."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        # Strip ```json ... ``` fences.
        lines = cleaned.split("\n")
        if lines[-1].strip() == "```":
            cleaned = "\n".join(lines[1:-1])
        else:
            cleaned = "\n".join(lines[1:])
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    components = [c for c in data.get("components", []) if isinstance(c, dict)]
    edges = [e for e in data.get("edges", []) if isinstance(e, dict)]
    return DiagramExtraction(components=components, edges=edges, raw=raw)


# ── pipeline integration ─────────────────────────────────────────────────────

async def extract_repo_diagrams(repo_path: Path | str) -> list[Artifact]:
    """Walk ``docs/`` for diagrams and return one Artifact per parsed image.

    Hooked into the pipeline as a post-extraction enrichment step. Limits
    itself to ``docs/**`` so a stray PNG in ``node_modules/`` doesn't burn
    a vision call.
    """
    repo = Path(repo_path)
    docs = repo / "docs"
    if not docs.is_dir():
        return []
    out: list[Artifact] = []
    for ext in _IMAGE_MEDIA_TYPES:
        for img in docs.rglob(f"*{ext}"):
            artifact = await extract_diagram(img)
            if artifact is not None:
                out.append(artifact)
    return out
