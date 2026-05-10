"""
ADR-0044 PR-0044-3: Per-chunk extractor.

Processes one MethodChunk at a time:
  1. Build prompt with header_context + import_context + body.
  2. LLM call, max_tokens=600.
  3. Validate JSON via Pydantic; salvage partial via _salvage helpers.
  4. If > 8 edges emitted, run follow-up calls (max 3) for remaining edges.

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
from companybrain.pipeline.code_chunker import MethodChunk
from companybrain.pipeline.lookup_tool import LookupTool, get_symbol_index

log = structlog.get_logger(__name__)

MAX_EDGES_PER_CALL = 8
MAX_FOLLOWUP_CALLS = 3
MAX_TOKENS_PER_CALL = 600


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
    entity: Optional[ExtractedChunkEntity]  # set for per_method; None for batch/whole_file
    edges: list[ExtractedEdge]
    cost_usd: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    lookup_calls: int = 0
    latency_ms: float = 0.0
    attempt: int = 1
    error: Optional[str] = None
    # ADR-0046: batch/whole-file strategies emit multiple entities per LLM call.
    entities: list[ExtractedChunkEntity] = field(default_factory=list)
    strategy_chosen: str = "per_method"

    def all_entities(self) -> list[ExtractedChunkEntity]:
        """Return all entities from this result regardless of strategy."""
        if self.entities:
            return self.entities
        if self.entity:
            return [self.entity]
        return []


# ── System prompt ─────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You will receive a single method (or top-level declaration) extracted from a larger codebase file.
The input has three clearly labelled sections:

  [IMPORTS]      — the file's import/use statements (context only, not the target)
  [CLASS HEADER] — class signature + field declarations (context only, not the target)
  [TARGET METHOD] — the specific method you must describe (THIS is the entity)

Your job: return ONE entity describing the TARGET METHOD, plus at most {max_edges} edges \
that originate from it.

Important scoping rules:
- The entity is the TARGET METHOD, not the class and not the file.
- Calls to methods listed in [SIBLING METHODS] are calls within the same class. \
  Emit them as CALLS edges with confidence ≤ 0.7 (internal helpers are low-value).
- Calls to types/methods NOT in [SIBLING METHODS] and not in [IMPORTS] are likely \
  external services or cross-class dependencies — emit those with higher confidence.
- Do NOT emit an edge for the logger, trivial getters/setters, null checks, or test setup.

The entity_type must be one of:
  ApiEndpoint | Function | InterfaceMethod | Class | DatabaseQuery | DatabaseTable |
  DatabaseColumn | SchemaField | ExternalService | ConfigKey | SharedType | FrontendComponent

The edge_type values must come from the canonical taxonomy:
{edge_types}

Output strict JSON — no prose, no markdown, no comments:

{{
  "entity": {{
    "entity_type": "<type>",
    "name": "<fully qualified name>",
    "signature": "<method signature line>",
    "confidence": <0.5-1.0>,
    "query_text": "<full verbatim SQL/JPQL if DatabaseQuery, else omit>",
    "code_snippet": "<key 1-2 line excerpt from body>"
  }},
  "edges": [
    {{"edge_type": "<type>", "target": "<name or URN>", "confidence": <0.5-1.0>, "evidence": "<1-line reason>"}}
  ]
}}

Confidence guide: 1.0 = explicitly defined in TARGET METHOD, 0.8 = clearly referenced, 0.6 = inferred, ≤0.7 = same-class internal call.
For DatabaseQuery: query_text must be the full verbatim SQL — never truncate it.
""".strip()

_FOLLOWUP_PROMPT = """\
The method above has more edges than fit in the first response.
The following edge types have already been emitted: {already_emitted}

Return ONLY the remaining edges as JSON — same schema, no entity field:
{{"edges": [...]}}

Emit at most {max_edges} edges.
""".strip()

# ── ADR-0046 D2: WHOLE_FILE system prompt ─────────────────────────────────────
_WHOLE_FILE_SYSTEM_PROMPT = """\
You will receive the complete source file for a single class or module.

Your job: return ONE entity for EACH meaningful method in the file, plus the
edges originating from each method.

Rules:
- Skip trivial methods: empty bodies, plain getters/setters, lombok-generated,
  toString/equals/hashCode overrides, and unsupported-operation stubs.
- For DatabaseQuery entities, query_text must be the FULL verbatim SQL/JPQL.
- Do NOT emit an edge for loggers, null checks, or test setup.

The entity_type must be one of:
  ApiEndpoint | Function | InterfaceMethod | Class | DatabaseQuery | DatabaseTable |
  DatabaseColumn | SchemaField | ExternalService | ConfigKey | SharedType | FrontendComponent

The edge_type values must come from the canonical taxonomy:
{edge_types}

Output strict JSON — no prose, no markdown, no comments:

{{
  "methods": {{
    "ClassName.methodName": {{
      "entity": {{
        "entity_type": "<type>",
        "name": "<fully qualified name>",
        "signature": "<method signature line>",
        "confidence": <0.5-1.0>,
        "query_text": "<full verbatim SQL/JPQL if DatabaseQuery, else omit>",
        "code_snippet": "<key 1-2 line excerpt>"
      }},
      "edges": [
        {{"edge_type": "<type>", "target": "<name or URN>", "confidence": <0.5-1.0>, "evidence": "<1-line reason>"}}
      ]
    }}
  }}
}}

Use the actual qualified name (e.g. "PayerService.getPayerCompetitors") as the key.
""".strip()

# ── ADR-0046 D2: BATCHED_METHODS system prompt ────────────────────────────────
_BATCHED_SYSTEM_PROMPT = """\
You will receive a batch of methods from the SAME class, each delimited by
[METHOD: ClassName.methodName].

Your job: return ONE entity per method in this batch, in the SAME ORDER as
the methods appear. Output a JSON object keyed by the method's qualified name.

Rules:
- Skip trivial methods (empty, getter/setter, lombok-generated, etc.) by
  omitting them from the output entirely.
- Calls between methods listed in [SIBLING METHODS] are internal — emit them
  as CALLS edges with confidence ≤ 0.7.
- For DatabaseQuery entities, query_text must be the full verbatim SQL.

The entity_type must be one of:
  ApiEndpoint | Function | InterfaceMethod | Class | DatabaseQuery | DatabaseTable |
  DatabaseColumn | SchemaField | ExternalService | ConfigKey | SharedType | FrontendComponent

The edge_type values must come from the canonical taxonomy:
{edge_types}

Output strict JSON — no prose, no markdown, no comments:

{{
  "methods": {{
    "ClassName.methodA": {{
      "entity": {{
        "entity_type": "<type>",
        "name": "<fully qualified name>",
        "signature": "<signature line>",
        "confidence": <0.5-1.0>,
        "query_text": "<full SQL if DatabaseQuery, else omit>",
        "code_snippet": "<1-2 key lines>"
      }},
      "edges": [
        {{"edge_type": "<type>", "target": "<name or URN>", "confidence": <0.5-1.0>, "evidence": "<reason>"}}
      ]
    }},
    "ClassName.methodB": {{ ... }}
  }}
}}
""".strip()

# Max tokens for batch/whole-file calls — more output needed since multiple entities.
MAX_TOKENS_BATCH = 2000
MAX_TOKENS_WHOLE_FILE = 3000


class ChunkExtractor:
    """
    Extracts one entity + edges from one MethodChunk using a focused LLM call.
    """

    async def extract(
        self,
        chunk: MethodChunk,
        lookup_tool: Optional[LookupTool] = None,
    ) -> ChunkResult:
        start = time.monotonic()
        if lookup_tool is None:
            lookup_tool = LookupTool(get_symbol_index())

        strategy = getattr(chunk, "strategy", "per_method")

        # Dispatch to the right extractor based on ADR-0046 strategy.
        if strategy == "whole_file":
            return await self._extract_whole_file(chunk, lookup_tool, start)
        if strategy == "batched_methods":
            return await self._extract_batch(chunk, lookup_tool, start)
        # Default: per_method (original path)
        return await self._extract_per_method(chunk, lookup_tool, start)

    async def _extract_per_method(
        self,
        chunk: MethodChunk,
        lookup_tool: LookupTool,
        start: float,
    ) -> ChunkResult:
        prompt_text = self._build_prompt(chunk)
        system = _SYSTEM_PROMPT.format(
            max_edges=MAX_EDGES_PER_CALL,
            edge_types=render_prompt_reference(),
        )

        try:
            provider = get_provider()
            # AnthropicProvider.chat() reads the system prompt from a
            # ChatMessage(role='system') in the messages list — it does NOT
            # accept a top-level `system=` kwarg. Passing one raised
            # 'unexpected keyword argument system' on every chunk and
            # the entire queue failed at attempt=1.
            resp = await provider.chat(
                messages=[
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="user",   content=prompt_text),
                ],
                role=TaskRole.FAST,
                max_tokens=MAX_TOKENS_PER_CALL,
            )
            raw = resp.content.strip()
            cost = resp.cost_usd if hasattr(resp, "cost_usd") else 0.0
            in_tok = resp.input_tokens if hasattr(resp, "input_tokens") else 0
            out_tok = resp.output_tokens if hasattr(resp, "output_tokens") else 0

            entity, edges = self._parse_response(raw, chunk)

            # If > 8 edges were hinted, run follow-up calls
            if entity is not None and len(edges) >= MAX_EDGES_PER_CALL:
                edges, extra_cost, extra_in, extra_out = await self._collect_remaining_edges(
                    chunk, prompt_text, edges, provider, system,
                )
                cost += extra_cost
                in_tok += extra_in
                out_tok += extra_out

            latency_ms = (time.monotonic() - start) * 1000
            log.info(
                "extraction_chunk",
                file=chunk.file_path,
                qname=chunk.qname,
                body_chars=len(chunk.body),
                entities_emitted=1 if entity else 0,
                edges_emitted=len(edges),
                cost_usd=round(cost, 6),
                input_tokens=in_tok,
                output_tokens=out_tok,
                latency_ms=round(latency_ms, 1),
                lookup_calls=lookup_tool.calls_used,
                status="done" if entity else "empty",
                strategy_chosen="per_method",
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
                strategy_chosen="per_method",
            )

        except Exception as exc:
            latency_ms = (time.monotonic() - start) * 1000
            log.error(
                "extraction_chunk",
                file=chunk.file_path,
                qname=chunk.qname,
                body_chars=len(chunk.body),
                entities_emitted=0,
                edges_emitted=0,
                cost_usd=0,
                input_tokens=0,
                output_tokens=0,
                latency_ms=round(latency_ms, 1),
                lookup_calls=lookup_tool.calls_used,
                status="failed",
                strategy_chosen="per_method",
                attempt=1,
                error=str(exc),
            )
            return ChunkResult(
                chunk=chunk,
                entity=None,
                edges=[],
                latency_ms=latency_ms,
                error=str(exc),
                strategy_chosen="per_method",
            )

    async def _extract_whole_file(
        self,
        chunk: MethodChunk,
        lookup_tool: LookupTool,
        start: float,
    ) -> ChunkResult:
        """One LLM call for the entire file — returns multiple entities."""
        system = _WHOLE_FILE_SYSTEM_PROMPT.format(edge_types=render_prompt_reference())
        parts = []
        if chunk.import_context.strip():
            parts.append(f"[IMPORTS]\n{chunk.import_context.strip()}")
        parts.append(f"[FILE: {chunk.qname}]\n{chunk.body.strip()}")
        prompt_text = "\n\n".join(parts)

        try:
            provider = get_provider()
            resp = await provider.chat(
                messages=[
                    ChatMessage(role="system", content=system),
                    ChatMessage(role="user",   content=prompt_text),
                ],
                role=TaskRole.FAST,
                max_tokens=MAX_TOKENS_WHOLE_FILE,
            )
            raw = resp.content.strip()
            cost = getattr(resp, "cost_usd", 0.0)
            in_tok = getattr(resp, "input_tokens", 0)
            out_tok = getattr(resp, "output_tokens", 0)

            entities, edges = self._parse_multi_response(raw, chunk)
            latency_ms = (time.monotonic() - start) * 1000
            log.info(
                "extraction_chunk",
                file=chunk.file_path,
                qname=chunk.qname,
                body_chars=len(chunk.body),
                entities_emitted=len(entities),
                edges_emitted=len(edges),
                cost_usd=round(cost, 6),
                input_tokens=in_tok,
                output_tokens=out_tok,
                latency_ms=round(latency_ms, 1),
                lookup_calls=lookup_tool.calls_used,
                status="done" if entities else "empty",
                strategy_chosen="whole_file",
                attempt=1,
            )
            return ChunkResult(
                chunk=chunk,
                entity=None,
                entities=entities,
                edges=edges,
                cost_usd=cost,
                input_tokens=in_tok,
                output_tokens=out_tok,
                lookup_calls=lookup_tool.calls_used,
                latency_ms=latency_ms,
                strategy_chosen="whole_file",
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - start) * 1000
            log.error(
                "extraction_chunk",
                file=chunk.file_path,
                qname=chunk.qname,
                status="failed",
                strategy_chosen="whole_file",
                error=str(exc),
            )
            return ChunkResult(
                chunk=chunk,
                entity=None,
                edges=[],
                latency_ms=latency_ms,
                error=str(exc),
                strategy_chosen="whole_file",
            )

    async def _extract_batch(
        self,
        chunk: MethodChunk,
        lookup_tool: LookupTool,
        start: float,
    ) -> ChunkResult:
        """One LLM call for a batch of small methods — returns multiple entities."""
        system = _BATCHED_SYSTEM_PROMPT.format(edge_types=render_prompt_reference())
        parts = []
        if chunk.import_context.strip():
            parts.append(f"[IMPORTS]\n{chunk.import_context.strip()}")
        if chunk.header_context.strip():
            parts.append(f"[CLASS HEADER]\n{chunk.header_context.strip()}")
        siblings = getattr(chunk, "sibling_signatures", None) or []
        if siblings:
            sig_list = "\n".join(f"  - {s}" for s in siblings[:20])
            parts.append(f"[SIBLING METHODS]\n{sig_list}")
        parts.append(f"[BATCH OF METHODS]\n{chunk.body.strip()}")
        prompt_text = "\n\n".join(parts)

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
            raw = resp.content.strip()
            cost = getattr(resp, "cost_usd", 0.0)
            in_tok = getattr(resp, "input_tokens", 0)
            out_tok = getattr(resp, "output_tokens", 0)

            entities, edges = self._parse_multi_response(raw, chunk)
            latency_ms = (time.monotonic() - start) * 1000
            log.info(
                "extraction_chunk",
                file=chunk.file_path,
                qname=chunk.qname,
                body_chars=len(chunk.body),
                entities_emitted=len(entities),
                edges_emitted=len(edges),
                cost_usd=round(cost, 6),
                input_tokens=in_tok,
                output_tokens=out_tok,
                latency_ms=round(latency_ms, 1),
                lookup_calls=lookup_tool.calls_used,
                status="done" if entities else "empty",
                strategy_chosen="batched_methods",
                attempt=1,
            )
            return ChunkResult(
                chunk=chunk,
                entity=None,
                entities=entities,
                edges=edges,
                cost_usd=cost,
                input_tokens=in_tok,
                output_tokens=out_tok,
                lookup_calls=lookup_tool.calls_used,
                latency_ms=latency_ms,
                strategy_chosen="batched_methods",
            )
        except Exception as exc:
            latency_ms = (time.monotonic() - start) * 1000
            log.error(
                "extraction_chunk",
                file=chunk.file_path,
                qname=chunk.qname,
                status="failed",
                strategy_chosen="batched_methods",
                error=str(exc),
            )
            return ChunkResult(
                chunk=chunk,
                entity=None,
                edges=[],
                latency_ms=latency_ms,
                error=str(exc),
                strategy_chosen="batched_methods",
            )

    # ── Prompt builder ─────────────────────────────────────────────────────────

    def _build_prompt(self, chunk: MethodChunk) -> str:
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

    # ── Multi-entity parser (batch / whole-file) ───────────────────────────────

    def _parse_multi_response(
        self, raw: str, chunk: MethodChunk,
    ) -> tuple[list[ExtractedChunkEntity], list[ExtractedEdge]]:
        """Parse a `{"methods": {"qname": {"entity": ..., "edges": [...]}}}` response."""
        try:
            data = _parse_json(raw)
        except Exception:
            return [], []

        methods_map: dict = data.get("methods", {})
        all_entities: list[ExtractedChunkEntity] = []
        all_edges: list[ExtractedEdge] = []

        for qname, method_data in methods_map.items():
            raw_entity = method_data.get("entity") or {}
            if not raw_entity:
                continue
            entity = ExtractedChunkEntity(
                entity_type=raw_entity.get("entity_type", "Function"),
                name=raw_entity.get("name", qname),
                qname=qname,
                file_path=chunk.file_path,
                signature=raw_entity.get("signature", ""),
                confidence=float(raw_entity.get("confidence", 0.8)),
                query_text=raw_entity.get("query_text", ""),
                code_snippet=raw_entity.get("code_snippet", ""),
                language=chunk.language,
            )
            all_entities.append(entity)

            for raw_edge in method_data.get("edges", []):
                edge_type = raw_edge.get("edge_type", "")
                if edge_type not in EDGE_TYPES:
                    continue
                all_edges.append(ExtractedEdge(
                    edge_type=edge_type,
                    target=raw_edge.get("target", ""),
                    confidence=float(raw_edge.get("confidence", 0.8)),
                    evidence=raw_edge.get("evidence", ""),
                ))

        return all_entities, all_edges

    # ── Response parser ────────────────────────────────────────────────────────

    def _parse_response(
        self, raw: str, chunk: MethodChunk,
    ) -> tuple[Optional[ExtractedChunkEntity], list[ExtractedEdge]]:
        try:
            data = _parse_json(raw)
        except Exception:
            return None, []

        entity = None
        raw_entity = data.get("entity") or data.get("entities", [None])[0]
        if raw_entity:
            entity = ExtractedChunkEntity(
                entity_type=raw_entity.get("entity_type", "Function"),
                name=raw_entity.get("name", chunk.qname),
                qname=chunk.qname,
                file_path=chunk.file_path,
                signature=raw_entity.get("signature", ""),
                confidence=float(raw_entity.get("confidence", 0.8)),
                query_text=raw_entity.get("query_text", ""),
                code_snippet=raw_entity.get("code_snippet", ""),
                language=chunk.language,
            )

        edges: list[ExtractedEdge] = []
        for raw_edge in data.get("edges", []):
            edge_type = raw_edge.get("edge_type", "")
            if edge_type not in EDGE_TYPES:
                continue
            edges.append(ExtractedEdge(
                edge_type=edge_type,
                target=raw_edge.get("target", ""),
                confidence=float(raw_edge.get("confidence", 0.8)),
                evidence=raw_edge.get("evidence", ""),
            ))

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
        total_in = 0
        total_out = 0

        for _ in range(MAX_FOLLOWUP_CALLS):
            if len(all_edges) < MAX_EDGES_PER_CALL:
                break

            already = ", ".join({e.edge_type for e in all_edges})
            followup = _FOLLOWUP_PROMPT.format(
                already_emitted=already,
                max_edges=MAX_EDGES_PER_CALL,
            )
            try:
                # Same fix as the primary call: system goes inside messages,
                # not as a kwarg.
                resp = await provider.chat(
                    messages=[
                        ChatMessage(role="system",    content=system),
                        ChatMessage(role="user",      content=original_prompt),
                        ChatMessage(role="assistant", content=json.dumps({"edges": [e.__dict__ for e in initial_edges]})),
                        ChatMessage(role="user",      content=followup),
                    ],
                    role=TaskRole.FAST,
                    max_tokens=MAX_TOKENS_PER_CALL,
                )
                total_cost += getattr(resp, "cost_usd", 0.0)
                total_in += getattr(resp, "input_tokens", 0)
                total_out += getattr(resp, "output_tokens", 0)

                data = _parse_json(resp.content.strip())
                for raw_edge in data.get("edges", []):
                    et = raw_edge.get("edge_type", "")
                    if et in EDGE_TYPES:
                        all_edges.append(ExtractedEdge(
                            edge_type=et,
                            target=raw_edge.get("target", ""),
                            confidence=float(raw_edge.get("confidence", 0.8)),
                            evidence=raw_edge.get("evidence", ""),
                        ))
            except Exception:
                break

        return all_edges, total_cost, total_in, total_out


# ── JSON parsing helpers ──────────────────────────────────────────────────────

def _parse_json(raw: str) -> dict:
    """Parse JSON from raw LLM output, tolerating leading/trailing prose."""
    # Strip markdown fences
    raw = re.sub(r'^```(?:json)?\s*', '', raw.strip(), flags=re.MULTILINE)
    raw = re.sub(r'\s*```$', '', raw.strip(), flags=re.MULTILINE)

    # Try direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Salvage: find the first { ... } block
    start = raw.find("{")
    if start == -1:
        raise ValueError("No JSON object found")

    # Walk from the start brace, collect the longest valid prefix
    # by trying to close the object at each closing brace candidate.
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

    # Last resort: try appending closing braces to salvage a truncated object
    truncated = raw[start:]
    for closes in ["}", "}}", "}}}"]:
        try:
            return json.loads(truncated + closes)
        except json.JSONDecodeError:
            continue

    raise ValueError("Could not parse JSON")
