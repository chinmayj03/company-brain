"""ContextAgent — batched per-method extraction.

Receives a batch of method bodies (typically up to 8 same-class siblings),
returns entities + edges + business_context for the whole batch in
one LLM call.

Replaces the per-chunk path in chunk_extractor.py with a batched, cached
alternative. The system prompt is larger (~3k tokens) and benefits from
prompt caching — after the first call, repeated calls cost ~10% of the
system prompt tokens.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import structlog

from companybrain.edges.taxonomy import render_prompt_reference
from companybrain.llm import get_provider, ChatMessage, TaskRole
from companybrain.pipeline.chunk_extractor import ExtractedChunkEntity, ExtractedEdge

log = structlog.get_logger(__name__)

# ── System prompt (cached) ────────────────────────────────────────────────────
# This is the expensive prefix that benefits from prompt caching. Keep it
# stable across calls — any edit invalidates the cache.

_SYSTEM_PROMPT = """\
You are a code-context extractor.

For each method in the input, emit:
  - one entity (Method/Function/ApiEndpoint/etc.) with signature + code_snippet + query_text
  - all edges originating from that method

Entity types: ApiEndpoint | Function | InterfaceMethod | Class | DatabaseQuery |
  DatabaseTable | DatabaseColumn | SchemaField | ExternalService | ConfigKey |
  SharedType | FrontendComponent

Edge types (closed taxonomy):
{edge_types}

BusinessContext fields for each method:
  purpose (str), change_risk ("LOW"|"MEDIUM"|"HIGH"), data_sensitivity (str|null),
  invariants (list[str]), side_effects (list[str]), failure_modes (list[str]),
  owner_team (str|null)

Return a single compact JSON object — no prose, no markdown:
{{
  "results": [
    {{
      "qname": "ClassName.methodName",
      "entity": {{
        "entity_type": "Function",
        "name": "ClassName.methodName",
        "signature": "public ReturnType methodName(ArgType arg)",
        "confidence": 0.9,
        "query_text": "",
        "code_snippet": "key 1-2 line excerpt"
      }},
      "edges": [
        {{"edge_type": "CALLS", "target": "OtherClass.otherMethod", "confidence": 0.9, "evidence": "line 42"}}
      ],
      "business_context": {{
        "purpose": "...", "change_risk": "MEDIUM", "data_sensitivity": null,
        "invariants": [], "side_effects": [], "failure_modes": [], "owner_team": null
      }}
    }}
  ]
}}

Return exactly one entry per input <method> tag, in the same order.
Confidence: 1.0=defined, 0.8=referenced, 0.6=inferred, ≤0.7=same-class call.
For DatabaseQuery: query_text must be the FULL verbatim SQL — never truncate.
Do NOT emit edges for: loggers, trivial getters/setters, null checks, test setup.
"""


@dataclass
class ContextAgentResult:
    qname: str
    entity: Optional[ExtractedChunkEntity]
    edges: list[ExtractedEdge] = field(default_factory=list)
    business_context: dict = field(default_factory=dict)
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


class ContextAgent:
    """Batched, cache-aware extraction agent.

    Replaces ChunkExtractor's per-method loop with a single batched call per
    group of same-class methods. Designed to work with up to 8 methods per call
    (configurable via context_agent_batch_size in settings).
    """

    def __init__(self) -> None:
        self._provider = get_provider()

    async def extract_batch(
        self,
        chunks: list,           # list[MethodChunk]
        max_tokens: int = 4_000,
    ) -> list[ContextAgentResult]:
        if not chunks:
            return []

        start = time.monotonic()
        system = _SYSTEM_PROMPT.format(edge_types=render_prompt_reference())
        user = self._build_user_xml(chunks)

        log.info(
            "ContextAgent.extract_batch calling LLM",
            batch_size=len(chunks),
            language=chunks[0].language if chunks else "",
        )

        try:
            raw = await self._provider.chat_json(
                messages=[
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="user", content=user),
                ],
                role=TaskRole.FAST,
                max_tokens=max_tokens,
            )
            results = self._parse(raw, chunks)
        except Exception as exc:
            log.warning("ContextAgent.extract_batch: LLM call failed", error=str(exc))
            results = [self._empty_result(c.qname) for c in chunks]

        latency_ms = (time.monotonic() - start) * 1000
        log.info(
            "ContextAgent.extract_batch complete",
            batch_size=len(chunks),
            results=len(results),
            latency_ms=round(latency_ms, 1),
        )
        return results

    # ── helpers ───────────────────────────────────────────────────────────────

    def _build_user_xml(self, chunks: list) -> str:
        """Build the user message with shared class header + per-method bodies."""
        first = chunks[0]
        parts = [
            f'<class_header file="{first.file_path}" class="{first.qname.split(".")[0]}">',
            first.header_context or "",
            "</class_header>",
            "",
            "<imports>",
            first.import_context or "",
            "</imports>",
            "",
        ]
        for c in chunks:
            parts.extend([
                f'<method qname="{c.qname}" lang="{c.language}">',
                c.body,
                "</method>",
                "",
            ])
        return "\n".join(parts)

    def _parse(self, raw: str, chunks: list) -> list[ContextAgentResult]:
        """Parse the LLM's JSON response into ContextAgentResult objects."""
        cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip(), flags=re.MULTILINE)
        cleaned = re.sub(r"```\s*$", "", cleaned, flags=re.MULTILINE).strip()

        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            m = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group())
                except json.JSONDecodeError:
                    log.warning("ContextAgent: cannot parse JSON response", raw=raw[:300])
                    return [self._empty_result(c.qname) for c in chunks]
            else:
                log.warning("ContextAgent: no JSON in response", raw=raw[:300])
                return [self._empty_result(c.qname) for c in chunks]

        raw_results = data.get("results", [])
        if not isinstance(raw_results, list):
            raw_results = []

        # Align by index (same order as input); pad with empty results if truncated
        out: list[ContextAgentResult] = []
        for i, chunk in enumerate(chunks):
            if i < len(raw_results) and isinstance(raw_results[i], dict):
                out.append(self._to_result(raw_results[i], chunk))
            else:
                out.append(self._empty_result(chunk.qname))
        return out

    def _to_result(self, item: dict, chunk) -> ContextAgentResult:
        raw_entity = item.get("entity") or {}
        if not isinstance(raw_entity, dict):
            raw_entity = {}

        entity = ExtractedChunkEntity(
            entity_type=raw_entity.get("entity_type", "Function"),
            name=raw_entity.get("name", chunk.qname),
            qname=item.get("qname", chunk.qname),
            file_path=chunk.file_path,
            signature=raw_entity.get("signature", ""),
            confidence=float(raw_entity.get("confidence", 0.9)),
            query_text=raw_entity.get("query_text", "") or "",
            code_snippet=raw_entity.get("code_snippet", "") or "",
            language=chunk.language,
        )

        raw_edges = item.get("edges") or []
        if not isinstance(raw_edges, list):
            raw_edges = []
        edges = []
        for e in raw_edges:
            if not isinstance(e, dict):
                continue
            edges.append(ExtractedEdge(
                edge_type=e.get("edge_type", "CALLS"),
                target=e.get("target", ""),
                confidence=float(e.get("confidence", 0.8)),
                evidence=e.get("evidence", "") or "",
            ))

        business_context = item.get("business_context") or {}
        if not isinstance(business_context, dict):
            business_context = {}

        return ContextAgentResult(
            qname=item.get("qname", chunk.qname),
            entity=entity,
            edges=edges,
            business_context=business_context,
        )

    def _empty_result(self, qname: str) -> ContextAgentResult:
        return ContextAgentResult(qname=qname, entity=None, edges=[])
