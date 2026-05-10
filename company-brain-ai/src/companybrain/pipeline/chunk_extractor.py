"""
ADR-0047: Per-chunk / batch-aware extractor.

Processes one ChunkBatch at a time:
  - Single-chunk batch  → one LLM call returning one entity + N edges.
  - Multi-chunk batch   → one LLM call returning a JSON array of entities
                          (one per chunk), each with its own edges.

Language-agnostic: prompts reference structural concepts (method, class, imports)
not framework-specific terms. The `language` field in telemetry tells operators
what they're looking at.

Cost target: ~$0.0005 per chunk on claude-haiku-4-5.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional

import structlog

from companybrain.edges.taxonomy import EDGE_TYPES, render_prompt_reference
from companybrain.llm import get_provider, TaskRole, ChatMessage
from companybrain.pipeline.chunk_batcher import ChunkBatch
from companybrain.pipeline.code_chunker import MethodChunk
from companybrain.pipeline.lookup_tool import LookupTool, get_symbol_index

log = structlog.get_logger(__name__)

MAX_EDGES_PER_CALL = 8
MAX_FOLLOWUP_CALLS = 3
MAX_TOKENS_SINGLE   = 600
MAX_TOKENS_BATCH    = 1200   # batched call returns an array — more headroom


# ── Output models ──────────────────────────────────────────────────────────────

@dataclass
class ExtractedEdge:
    edge_type: str
    target: str
    confidence: float = 0.8
    evidence: str = ""


@dataclass
class ExtractedChunkEntity:
    entity_type: str
    name: str
    qname: str
    file_path: str
    signature: str = ""
    confidence: float = 0.9
    query_text: str = ""
    code_snippet: str = ""
    language: str = ""


@dataclass
class ChunkResult:
    chunk: MethodChunk
    entity: Optional[ExtractedChunkEntity]
    edges: list[ExtractedEdge]
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    lookup_calls: int = 0
    latency_ms: float = 0.0
    attempt: int = 1
    error: Optional[str] = None


@dataclass
class BatchResult:
    batch: ChunkBatch
    results: list[ChunkResult]
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: float = 0.0


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_SINGLE = """\
You will receive a single method (or top-level declaration) extracted from a source file.
The input has clearly labelled sections:

  [IMPORTS]       — the file's import / use statements (context only, not the target)
  [CLASS HEADER]  — class signature + field declarations (context only, not the target)
  [SIBLING METHODS] — other method signatures in the same class (internal helpers)
  [TARGET METHOD] — the specific method you must describe (THIS is the entity)

Return ONE entity describing the TARGET METHOD and at most {max_edges} edges from it.

Scoping rules:
- The entity is the TARGET METHOD, not the class and not the file.
- Calls to SIBLING METHODS are same-class internal calls — emit with confidence ≤ 0.7.
- Calls to types/methods not in [SIBLING METHODS] are likely cross-boundary — higher confidence.
- Do NOT emit edges for: loggers, trivial getters/setters, null checks, test setup.

Entity types: ApiEndpoint | Function | InterfaceMethod | Class | DatabaseQuery |
  DatabaseTable | DatabaseColumn | SchemaField | ExternalService | ConfigKey |
  SharedType | FrontendComponent

Edge types must come from the canonical taxonomy:
{edge_types}

Output strict JSON — no prose, no markdown:

{{
  "entity": {{
    "entity_type": "<type>",
    "name": "<fully qualified name>",
    "signature": "<method signature line>",
    "confidence": <0.5-1.0>,
    "query_text": "<full verbatim SQL/query if DatabaseQuery, else omit>",
    "code_snippet": "<key 1-2 line excerpt>"
  }},
  "edges": [
    {{"edge_type": "<type>", "target": "<name>", "confidence": <0.5-1.0>, "evidence": "<1-line reason>"}}
  ]
}}

Confidence guide: 1.0 = defined in TARGET, 0.8 = clearly referenced, 0.6 = inferred,
≤0.7 = same-class internal call.
For DatabaseQuery: query_text must be the FULL verbatim SQL — never truncate it.
""".strip()

_SYSTEM_BATCH = """\
You will receive {n} methods from the SAME class, each in a [METHOD N] section.
The [IMPORTS] and [CLASS HEADER] sections apply to all of them.

For each method return exactly one entity + edges.

Return a JSON array with exactly {n} items (one per method, same order):

[
  {{
    "entity": {{ "entity_type": "...", "name": "...", "signature": "...",
                 "confidence": 0.9, "query_text": "...", "code_snippet": "..." }},
    "edges":  [ {{"edge_type": "...", "target": "...", "confidence": 0.8, "evidence": "..."}} ]
  }},
  ...
]

Entity types: ApiEndpoint | Function | InterfaceMethod | Class | DatabaseQuery |
  DatabaseTable | DatabaseColumn | SchemaField | ExternalService | ConfigKey |
  SharedType | FrontendComponent

Edge types: {edge_types}

Do NOT emit edges for: loggers, trivial accessors, null checks, test setup.
For DatabaseQuery: query_text must be the FULL verbatim query — never truncate.
Same confidence guide: 1.0 defined, 0.8 referenced, 0.6 inferred, ≤0.7 same-class call.
""".strip()

_FOLLOWUP_PROMPT = """\
The method above has more edges than fit in the first response.
Already emitted edge types: {already_emitted}

Return ONLY remaining edges as JSON — same schema, no entity field:
{{"edges": [...]}}

Emit at most {max_edges} edges.
""".strip()


class ChunkExtractor:
    """
    Extracts entities + edges from a ChunkBatch using a single focused LLM call.
    Single-chunk batches use the single-entity schema.
    Multi-chunk batches use the array schema (one entity per chunk, same order).
    """

    async def extract_batch(
        self,
        batch: ChunkBatch,
        lookup_tool: Optional[LookupTool] = None,
    ) -> BatchResult:
        start = time.monotonic()
        if lookup_tool is None:
            lookup_tool = LookupTool(get_symbol_index())

        if batch.is_batched:
            results, cost, in_tok, out_tok = await self._extract_multi(batch, lookup_tool)
        else:
            single_result = await self.extract(batch.chunks[0], lookup_tool)
            results = [single_result]
            cost    = single_result.cost_usd
            in_tok  = single_result.input_tokens
            out_tok = single_result.output_tokens

        latency_ms = (time.monotonic() - start) * 1000
        language = batch.chunks[0].language if batch.chunks else ""

        log.info(
            "chunk_extractor.batch",
            batch_size=len(batch.chunks),
            strategy="batched" if batch.is_batched else "single",
            language=language,
            rationale=batch.rationale,
            cost_usd=round(cost, 6),
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=round(latency_ms, 1),
        )
        return BatchResult(
            batch=batch,
            results=results,
            cost_usd=cost,
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=latency_ms,
        )

    async def extract(
        self,
        chunk: MethodChunk,
        lookup_tool: Optional[LookupTool] = None,
    ) -> ChunkResult:
        start = time.monotonic()
        if lookup_tool is None:
            lookup_tool = LookupTool(get_symbol_index())

        prompt_text = self._build_single_prompt(chunk)
        system = _SYSTEM_SINGLE.format(
            max_edges=MAX_EDGES_PER_CALL,
            edge_types=render_prompt_reference(),
        )

        try:
            provider = get_provider()
            resp = await provider.chat(
                messages=[
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="user",   content=prompt_text),
                ],
                role=TaskRole.FAST,
                max_tokens=MAX_TOKENS_SINGLE,
            )
            raw = resp.content.strip()
            cost   = getattr(resp, "cost_usd", 0.0)
            in_tok = getattr(resp, "input_tokens", 0)
            out_tok = getattr(resp, "output_tokens", 0)

            entity, edges = self._parse_single_response(raw, chunk)

            if entity is not None and len(edges) >= MAX_EDGES_PER_CALL:
                edges, extra_cost, extra_in, extra_out = await self._collect_remaining_edges(
                    chunk, prompt_text, edges, provider, system,
                )
                cost    += extra_cost
                in_tok  += extra_in
                out_tok += extra_out

            latency_ms = (time.monotonic() - start) * 1000

            log.info(
                "chunk_extractor.single",
                file=chunk.file_path,
                qname=chunk.qname,
                language=chunk.language,
                body_chars=len(chunk.body),
                entities_emitted=1 if entity else 0,
                edges_emitted=len(edges),
                cost_usd=round(cost, 6),
                input_tokens=in_tok,
                output_tokens=out_tok,
                latency_ms=round(latency_ms, 1),
                lookup_calls=lookup_tool.calls_used,
                strategy="single",
                status="done" if entity else "empty",
                attempt=1,
            )

            return ChunkResult(
                chunk=chunk,
                entity=entity,
                edges=edges,
                cost_usd=cost,
                input_tokens=in_tok,
                output_tokens=out_tok,
                lookup_calls=lookup_tool.calls_used,
                latency_ms=latency_ms,
            )

        except Exception as exc:
            latency_ms = (time.monotonic() - start) * 1000
            log.error(
                "chunk_extractor.single",
                file=chunk.file_path,
                qname=chunk.qname,
                language=chunk.language,
                body_chars=len(chunk.body),
                entities_emitted=0,
                edges_emitted=0,
                cost_usd=0,
                input_tokens=0,
                output_tokens=0,
                latency_ms=round(latency_ms, 1),
                lookup_calls=lookup_tool.calls_used,
                strategy="single",
                status="failed",
                attempt=1,
                error=str(exc),
            )
            return ChunkResult(
                chunk=chunk,
                entity=None,
                edges=[],
                latency_ms=latency_ms,
                error=str(exc),
            )

    # ── Multi-chunk batch path ─────────────────────────────────────────────────

    async def _extract_multi(
        self,
        batch: ChunkBatch,
        lookup_tool: LookupTool,
    ) -> tuple[list[ChunkResult], float, int, int]:
        chunks = batch.chunks
        prompt_text = self._build_batch_prompt(chunks)
        system = _SYSTEM_BATCH.format(
            n=len(chunks),
            edge_types=render_prompt_reference(),
        )

        results: list[ChunkResult] = []
        cost = 0.0
        in_tok = 0
        out_tok = 0

        try:
            provider = get_provider()
            resp = await provider.chat(
                messages=[
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="user",   content=prompt_text),
                ],
                role=TaskRole.FAST,
                max_tokens=MAX_TOKENS_BATCH,
            )
            cost    = getattr(resp, "cost_usd", 0.0)
            in_tok  = getattr(resp, "input_tokens", 0)
            out_tok = getattr(resp, "output_tokens", 0)

            items = _parse_json_array(resp.content.strip())

            for i, chunk in enumerate(chunks):
                item = items[i] if i < len(items) else {}
                entity = _entity_from_dict(item.get("entity") or {}, chunk)
                edges  = _edges_from_list(item.get("edges") or [])
                results.append(ChunkResult(
                    chunk=chunk,
                    entity=entity,
                    edges=edges,
                    cost_usd=cost / len(chunks),  # amortised per chunk
                    input_tokens=in_tok // len(chunks),
                    output_tokens=out_tok // len(chunks),
                ))

        except Exception as exc:
            log.error(
                "chunk_extractor.batch_failed",
                batch_size=len(chunks),
                error=str(exc),
            )
            for chunk in chunks:
                results.append(ChunkResult(
                    chunk=chunk,
                    entity=None,
                    edges=[],
                    error=str(exc),
                ))

        return results, cost, in_tok, out_tok

    # ── Prompt builders ────────────────────────────────────────────────────────

    def _build_single_prompt(self, chunk: MethodChunk) -> str:
        parts: list[str] = []
        if chunk.import_context.strip():
            parts.append(f"[IMPORTS]\n{chunk.import_context.strip()}")
        if chunk.header_context.strip():
            parts.append(f"[CLASS HEADER]\n{chunk.header_context.strip()}")
        siblings = getattr(chunk, "sibling_signatures", None) or []
        if siblings:
            sig_list = "\n".join(f"  - {s}" for s in siblings[:20])
            parts.append(f"[SIBLING METHODS] (same class — calls to these are internal)\n{sig_list}")
        parts.append(f"[TARGET METHOD] ({chunk.qname})\n{chunk.body.strip()}")
        return "\n\n".join(parts)

    def _build_batch_prompt(self, chunks: list[MethodChunk]) -> str:
        parts: list[str] = []
        # Shared context from first chunk (all are same class)
        first = chunks[0]
        if first.import_context.strip():
            parts.append(f"[IMPORTS]\n{first.import_context.strip()}")
        if first.header_context.strip():
            parts.append(f"[CLASS HEADER]\n{first.header_context.strip()}")
        for i, chunk in enumerate(chunks, start=1):
            parts.append(f"[METHOD {i}] ({chunk.qname})\n{chunk.body.strip()}")
        return "\n\n".join(parts)

    # ── Response parsers ───────────────────────────────────────────────────────

    def _parse_single_response(
        self, raw: str, chunk: MethodChunk,
    ) -> tuple[Optional[ExtractedChunkEntity], list[ExtractedEdge]]:
        try:
            data = _parse_json(raw)
        except Exception:
            return None, []

        raw_entity = data.get("entity") or (data.get("entities") or [None])[0]
        entity = _entity_from_dict(raw_entity or {}, chunk) if raw_entity else None
        edges  = _edges_from_list(data.get("edges") or [])
        return entity, edges

    # ── Follow-up edge collection ──────────────────────────────────────────────

    async def _collect_remaining_edges(
        self,
        chunk: MethodChunk,
        original_prompt: str,
        initial_edges: list[ExtractedEdge],
        provider,
        system: str,
    ) -> tuple[list[ExtractedEdge], float, int, int]:
        all_edges = list(initial_edges)
        total_cost = 0.0
        total_in   = 0
        total_out  = 0

        for _ in range(MAX_FOLLOWUP_CALLS):
            if len(all_edges) < MAX_EDGES_PER_CALL:
                break

            already = ", ".join({e.edge_type for e in all_edges})
            followup = _FOLLOWUP_PROMPT.format(
                already_emitted=already,
                max_edges=MAX_EDGES_PER_CALL,
            )
            try:
                resp = await provider.chat(
                    messages=[
                        ChatMessage(role="system",    content=system),
                        ChatMessage(role="user",      content=original_prompt),
                        ChatMessage(role="assistant", content=json.dumps(
                            {"edges": [e.__dict__ for e in initial_edges]})),
                        ChatMessage(role="user",      content=followup),
                    ],
                    role=TaskRole.FAST,
                    max_tokens=MAX_TOKENS_SINGLE,
                )
                total_cost += getattr(resp, "cost_usd", 0.0)
                total_in   += getattr(resp, "input_tokens", 0)
                total_out  += getattr(resp, "output_tokens", 0)

                data = _parse_json(resp.content.strip())
                all_edges.extend(_edges_from_list(data.get("edges") or []))
            except Exception:
                break

        return all_edges, total_cost, total_in, total_out


# ── Shared entity / edge builders ─────────────────────────────────────────────

def _entity_from_dict(d: dict, chunk: MethodChunk) -> Optional[ExtractedChunkEntity]:
    if not d:
        return None
    return ExtractedChunkEntity(
        entity_type=d.get("entity_type", "Function"),
        name=d.get("name", chunk.qname),
        qname=chunk.qname,
        file_path=chunk.file_path,
        signature=d.get("signature", ""),
        confidence=float(d.get("confidence", 0.8)),
        query_text=d.get("query_text", ""),
        code_snippet=d.get("code_snippet", ""),
        language=chunk.language,
    )


def _edges_from_list(raw_edges: list) -> list[ExtractedEdge]:
    edges = []
    for raw in raw_edges:
        et = raw.get("edge_type", "")
        if et in EDGE_TYPES:
            edges.append(ExtractedEdge(
                edge_type=et,
                target=raw.get("target", ""),
                confidence=float(raw.get("confidence", 0.8)),
                evidence=raw.get("evidence", ""),
            ))
    return edges


# ── JSON parsing helpers ──────────────────────────────────────────────────────

def _parse_json(raw: str) -> dict:
    """Parse a JSON object from raw LLM output, tolerating leading/trailing prose."""
    raw = re.sub(r'^```(?:json)?\s*', '', raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r'\s*```$', '', raw.strip(), flags=re.MULTILINE)

    try:
        result = json.loads(raw)
        return result if isinstance(result, dict) else {}
    except json.JSONDecodeError:
        pass

    start = raw.find("{")
    if start == -1:
        raise ValueError("No JSON object found")

    depth = 0
    for i, ch in enumerate(raw[start:], start=start):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start : i + 1])
                except json.JSONDecodeError:
                    pass

    truncated = raw[start:]
    for closes in ["}", "}}", "}}}"]:
        try:
            return json.loads(truncated + closes)
        except json.JSONDecodeError:
            continue

    raise ValueError("Could not parse JSON")


def _parse_json_array(raw: str) -> list[dict]:
    """Parse a JSON array from raw LLM output; fall back to empty list."""
    raw = re.sub(r'^```(?:json)?\s*', '', raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r'\s*```$', '', raw.strip(), flags=re.MULTILINE)

    try:
        result = json.loads(raw)
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            # Wrapped: {"results": [...]} or {"entities": [...]}
            for key in ("results", "entities", "items", "data"):
                if isinstance(result.get(key), list):
                    return result[key]
    except json.JSONDecodeError:
        pass

    start = raw.find("[")
    if start == -1:
        return []

    depth = 0
    for i, ch in enumerate(raw[start:], start=start):
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(raw[start : i + 1])
                except json.JSONDecodeError:
                    pass

    return []
