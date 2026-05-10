"""extract_methods_from_class — batched ContextAgent extraction (ADR-0048)."""
from __future__ import annotations

import hashlib
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from companybrain.agents.context_agent import ContextAgent, ContextAgentResult
from companybrain.harness.tools import register_tool
from companybrain.llm.base import ToolParameter
from companybrain.pipeline.code_chunker import _LANGUAGE_MAP, MethodChunk
from companybrain.util.file_cache import FileCache


@register_tool(
    name="extract_methods_from_class",
    description=(
        "Run the batched ContextAgent extractor on a set of methods from one source "
        "file. Returns one result per requested method as "
        "{qname, entity_type, signature, edges:[{edge_type,target,confidence}], "
        "business_context}. Prefer one batched call per file over many per-method calls."
    ),
    parameters=[
        ToolParameter("file", "string", "Absolute path to the source file."),
        ToolParameter("methods", "array",
                      "List of method qnames to extract, e.g. ['UserService.findById']. "
                      "Must all live in the same file."),
        ToolParameter("language", "string",
                      "Source language (java, python, typescript, ...). "
                      "Inferred from file extension when omitted.",
                      required=False),
    ],
)
async def handler(args: dict[str, Any], context: dict[str, Any]) -> list[dict[str, Any]]:
    file_path = args["file"]
    qnames = list(args.get("methods") or [])
    if not qnames:
        return []

    raw_language = args.get("language") or _LANGUAGE_MAP.get(Path(file_path).suffix.lower(), "")
    language = raw_language or "unknown"

    cache = context.get("file_cache")
    if isinstance(cache, FileCache):
        body = cache.read(file_path)
    else:
        try:
            body = Path(file_path).read_text(encoding="utf-8", errors="ignore")
        except OSError as exc:
            return [{"qname": q, "error": f"could not read {file_path}: {exc}"} for q in qnames]

    chunks = [
        MethodChunk(
            file_path=file_path,
            qname=q,
            kind="method",
            body=body,
            header_context="",
            import_context="",
            body_hash=hashlib.sha256(body.encode("utf-8", errors="ignore")).hexdigest(),
            language=language,
        )
        for q in qnames
    ]

    agent = ContextAgent()
    results: list[ContextAgentResult] = await agent.extract_batch(chunks)
    return [_to_dict(r) for r in results]


def _to_dict(r: ContextAgentResult) -> dict[str, Any]:
    """Render one ContextAgentResult as a small JSON payload for the model."""
    out: dict[str, Any] = {
        "qname":            r.qname,
        "edges":            [_edge_to_dict(e) for e in r.edges],
        "business_context": r.business_context or {},
    }
    if r.entity is not None:
        out["entity_type"] = r.entity.entity_type
        out["signature"]   = r.entity.signature
        out["confidence"]  = r.entity.confidence
        out["query_text"]  = r.entity.query_text
    return out


def _edge_to_dict(e: Any) -> dict[str, Any]:
    return {
        "edge_type":  getattr(e, "edge_type", ""),
        "target":     getattr(e, "target", ""),
        "confidence": getattr(e, "confidence", 0.0),
        "evidence":   (getattr(e, "evidence", "") or "")[:200],
    }


def _iter_qnames(values: Iterable[Any]) -> list[str]:
    return [str(v) for v in values if v is not None]
