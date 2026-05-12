"""
Protobuf schema extractor (S4) — ADR-0058.

Parses ``.proto`` files into ProtoMessage / ProtoService / ProtoRpc entities.
The ADR mentions ``betterproto`` or grpc_tools but both pull a heavy
transitive dep tree; the .proto grammar is small enough that a
hand-rolled tokenizer is faster, cheaper to maintain, and dependency-free.

Scope:
- syntax = "proto2"|"proto3"
- package x.y.z;
- import "foo.proto";       (recorded but not resolved)
- message X { field decls; nested types }
- service S { rpc M (Req) returns (Resp); rpc M (stream X) returns (stream Y); }
- enum types are recorded as message-like entries with kind="enum" not emitted
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

from companybrain.extractors.base import Extractor
from companybrain.models.entities import (
    EDGE_IMPLEMENTS_RPC,
    ExtractedBatch,
    ProtoMessage,
    ProtoRpc,
    ProtoService,
    SchemaEdge,
    SchemaExtractedBatch,
)


# Strip line and block comments before any structural matching.
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.MULTILINE)
_MESSAGE_HEAD_RE = re.compile(r"\bmessage\s+(\w+)\s*\{")
_SERVICE_HEAD_RE = re.compile(r"\bservice\s+(\w+)\s*\{")
_RPC_RE = re.compile(
    r"\brpc\s+(?P<name>\w+)\s*\(\s*(?P<stream_in>stream\s+)?(?P<req>[\w.]+)\s*\)\s*"
    r"returns\s*\(\s*(?P<stream_out>stream\s+)?(?P<resp>[\w.]+)\s*\)"
    r"\s*(?:\{[^}]*\}|;)",
)
_FIELD_RE = re.compile(
    r"(?P<repeated>repeated\s+|optional\s+|required\s+)?"
    r"(?P<type>(?:map\s*<\s*[\w.]+\s*,\s*[\w.]+\s*>|[\w.]+))\s+"
    r"(?P<name>\w+)\s*=\s*(?P<number>\d+)\s*(?:\[[^\]]*\])?\s*;",
)


class ProtoExtractor:
    """Universal-extraction Extractor for ``.proto`` files."""

    kind = "schema_proto"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() == ".proto"

    def extract(self, path: Path, content: str, *, repo: str = "") -> ExtractedBatch:
        batch = SchemaExtractedBatch(file=str(path), repo=repo, extractor_kind=self.kind)
        cleaned = _strip_comments(content)

        pkg_match = _PACKAGE_RE.search(cleaned)
        package = pkg_match.group(1) if pkg_match else ""

        # Messages — find each "message X {" and grab its balanced body.
        for name, body in _iter_top_blocks(cleaned, _MESSAGE_HEAD_RE):
            fields = list(_iter_fields(body))
            batch.proto_messages.append(ProtoMessage(
                name=name,
                package=package,
                fields=fields,
                source_file=str(path),
                repo=repo,
            ))

        # Services and their RPCs.
        for svc_name, body in _iter_top_blocks(cleaned, _SERVICE_HEAD_RE):
            service = ProtoService(name=svc_name, package=package, source_file=str(path), repo=repo)
            batch.proto_services.append(service)
            for m in _RPC_RE.finditer(body):
                rpc = ProtoRpc(
                    name=m.group("name"),
                    service_urn=service.external_id,
                    request_type=m.group("req"),
                    response_type=m.group("resp"),
                    client_streaming=bool(m.group("stream_in")),
                    server_streaming=bool(m.group("stream_out")),
                    source_file=str(path),
                    repo=repo,
                )
                batch.proto_rpcs.append(rpc)
                # IMPLEMENTS_RPC edge will be added by schema_resolver once Java
                # gRPC service implementations are known. We emit a placeholder
                # edge from the service to the RPC so the graph keeps the
                # parent-child relationship visible.
                batch.edges.append(SchemaEdge(
                    edge_type=EDGE_IMPLEMENTS_RPC,
                    from_urn=rpc.external_id,
                    to_urn="",  # filled in later by resolver
                    evidence=f"{m.group('req')} → {m.group('resp')}",
                    confidence=0.0,
                ))

        out = batch.to_extracted_batch()
        setattr(out, "_schema_batch", batch)
        return out


def _strip_comments(content: str) -> str:
    s = _BLOCK_COMMENT_RE.sub("", content)
    s = _LINE_COMMENT_RE.sub("", s)
    return s


def _iter_top_blocks(content: str, header_re: re.Pattern) -> Iterable[tuple[str, str]]:
    """Yield ``(name, body)`` for each top-level ``<keyword> Name { ... }`` block.

    Walks the file with a brace-depth counter so nested ``message`` blocks
    inside other messages are kept inside their parent and not emitted as
    top-level entries.
    """
    i = 0
    while i < len(content):
        m = header_re.search(content, i)
        if not m:
            return
        # Must be at top-level: count unmatched '{' before m.start()
        if _brace_depth(content, 0, m.start()) != 0:
            i = m.end()
            continue
        name = m.group(1)
        body_start = m.end()        # right after '{'
        body_end = _matching_close(content, body_start)
        if body_end < 0:
            return
        yield name, content[body_start:body_end]
        i = body_end + 1


def _brace_depth(content: str, start: int, end: int) -> int:
    depth = 0
    in_string = False
    quote = ""
    j = start
    while j < end:
        ch = content[j]
        if in_string:
            if ch == "\\":
                j += 2
                continue
            if ch == quote:
                in_string = False
        elif ch in ("'", '"'):
            in_string = True
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
        j += 1
    return depth


def _matching_close(content: str, after_open: int) -> int:
    depth = 1
    j = after_open
    in_string = False
    quote = ""
    while j < len(content):
        ch = content[j]
        if in_string:
            if ch == "\\":
                j += 2
                continue
            if ch == quote:
                in_string = False
        elif ch in ("'", '"'):
            in_string = True
            quote = ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return j
        j += 1
    return -1


def _iter_fields(message_body: str) -> Iterable[dict]:
    """Yield ``{name, type, number, repeated}`` records for each field declared
    directly in ``message_body``. Skips nested messages / enums.
    """
    # Quick-and-dirty exclusion of nested blocks by zeroing them out.
    stripped = _zero_nested_blocks(message_body)
    for m in _FIELD_RE.finditer(stripped):
        kw = (m.group("repeated") or "").strip()
        yield {
            "name": m.group("name"),
            "type": _normalize_proto_type(m.group("type")),
            "number": int(m.group("number")),
            "repeated": kw == "repeated",
        }


def _normalize_proto_type(t: str) -> str:
    return " ".join(t.split())


def _zero_nested_blocks(body: str) -> str:
    """Replace nested {...} groups with spaces so field regex can't match
    declarations inside them."""
    out = list(body)
    depth = 0
    for i, ch in enumerate(body):
        if ch == "{":
            depth += 1
            out[i] = " "
        elif ch == "}":
            depth -= 1
            out[i] = " "
        elif depth > 0:
            out[i] = " "
    return "".join(out)
