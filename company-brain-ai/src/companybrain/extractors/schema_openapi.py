"""
OpenAPI / Swagger schema extractor (S3) — ADR-0058.

Recognises ``openapi*.yaml`` / ``openapi*.json`` / ``swagger.yaml`` files, parses
them into typed ``OpenAPIOperation`` and ``OpenAPISchema`` entities, and emits
``SCHEMA_REQUEST`` / ``SCHEMA_RESPONSE`` edges. The ``DOCUMENTS`` edge that
links an OpenAPI operation to its in-repo controller implementation is
resolved in ``schema_resolver`` after Spring/Express controllers have also
been seen.

We use PyYAML (already a project dep) for parsing and a tiny content sniff
to claim YAML/JSON files that look like an OpenAPI spec without colliding
with the generic ConfigExtractor.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

import yaml

from companybrain.extractors.base import Extractor
from companybrain.models.entities import (
    EDGE_SCHEMA_REQUEST,
    EDGE_SCHEMA_RESPONSE,
    ExtractedBatch,
    OpenAPIOperation,
    OpenAPISchema,
    SchemaEdge,
    SchemaExtractedBatch,
)


_HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})

# Filename hints (cheap, no I/O) that a YAML/JSON file is an OpenAPI spec.
_NAME_HINTS = (
    "openapi",
    "swagger",
)


class OpenAPIExtractor:
    """Extractor for OpenAPI 2/3 specs in YAML or JSON form."""

    kind = "schema_openapi"

    def supports(self, path: Path) -> bool:
        name = path.name.lower()
        suffix = path.suffix.lower()
        if suffix not in {".yaml", ".yml", ".json"}:
            return False
        # match openapi.yaml / openapi.json / swagger.yaml / openapi-*.yaml / *.openapi.yaml
        stem = name.rsplit(".", 1)[0]
        for hint in _NAME_HINTS:
            if hint in stem:
                return True
        return False

    def extract(self, path: Path, content: str, *, repo: str = "") -> ExtractedBatch:
        batch = SchemaExtractedBatch(file=str(path), repo=repo, extractor_kind=self.kind)
        spec = _load_spec(path, content)
        if not isinstance(spec, dict):
            out = batch.to_extracted_batch()
            setattr(out, "_schema_batch", batch)
            return out

        # Component / definition schemas (OpenAPI 3 vs 2)
        schemas_root = spec.get("components", {}).get("schemas", {})
        if not isinstance(schemas_root, dict):
            schemas_root = {}
        legacy = spec.get("definitions", {}) or {}
        if isinstance(legacy, dict):
            schemas_root = {**legacy, **schemas_root}

        for name, schema in schemas_root.items():
            if not isinstance(schema, dict):
                continue
            properties = schema.get("properties") or {}
            if not isinstance(properties, dict):
                properties = {}
            batch.openapi_schemas.append(OpenAPISchema(
                name=str(name),
                type=str(schema.get("type") or "object"),
                properties={k: (v if isinstance(v, dict) else {"type": str(v)}) for k, v in properties.items()},
                required=list(schema.get("required") or []),
                source_file=str(path),
                repo=repo,
            ))

        # Paths
        paths = spec.get("paths") or {}
        if not isinstance(paths, dict):
            paths = {}
        for raw_path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, op in methods.items():
                if method.lower() not in _HTTP_METHODS or not isinstance(op, dict):
                    continue
                request_ref = _request_schema_ref(op)
                response_refs = _response_schema_refs(op)
                operation = OpenAPIOperation(
                    operation_id=str(op.get("operationId") or ""),
                    method=method.upper(),
                    path=str(raw_path),
                    summary=str(op.get("summary") or ""),
                    description=str(op.get("description") or ""),
                    tags=[str(t) for t in (op.get("tags") or [])],
                    request_schema_ref=request_ref,
                    response_schemas={int(code): ref for code, ref in response_refs.items()},
                    source_file=str(path),
                    repo=repo,
                )
                batch.openapi_ops.append(operation)
                if request_ref:
                    batch.edges.append(SchemaEdge(
                        edge_type=EDGE_SCHEMA_REQUEST,
                        from_urn=operation.external_id,
                        to_urn=f"openapi_schema::{_short_name(request_ref)}",
                        evidence=request_ref,
                    ))
                for code, ref in response_refs.items():
                    batch.edges.append(SchemaEdge(
                        edge_type=EDGE_SCHEMA_RESPONSE,
                        from_urn=operation.external_id,
                        to_urn=f"openapi_schema::{_short_name(ref)}",
                        evidence=f"{code}: {ref}",
                    ))

        out = batch.to_extracted_batch()
        setattr(out, "_schema_batch", batch)
        return out


def _load_spec(path: Path, content: str) -> Any:
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            return json.loads(content)
        return yaml.safe_load(content)
    except Exception:
        return None


def _request_schema_ref(op: dict) -> Optional[str]:
    body = op.get("requestBody")
    if isinstance(body, dict):
        content = body.get("content") or {}
        if isinstance(content, dict):
            for media in content.values():
                if isinstance(media, dict):
                    ref = _schema_ref(media.get("schema") or {})
                    if ref:
                        return ref
    # OpenAPI 2 / Swagger style: parameters: [{ in: body, schema: ... }]
    for p in op.get("parameters") or []:
        if isinstance(p, dict) and p.get("in") == "body":
            ref = _schema_ref(p.get("schema") or {})
            if ref:
                return ref
    return None


def _response_schema_refs(op: dict) -> dict[str, str]:
    out: dict[str, str] = {}
    responses = op.get("responses") or {}
    if not isinstance(responses, dict):
        return out
    for code, resp in responses.items():
        if not isinstance(resp, dict) or code == "default":
            continue
        ref = ""
        # OpenAPI 3
        content = resp.get("content") or {}
        if isinstance(content, dict):
            for media in content.values():
                if isinstance(media, dict):
                    ref = _schema_ref(media.get("schema") or {})
                    if ref:
                        break
        # OpenAPI 2 / Swagger style: schema directly on response
        if not ref:
            ref = _schema_ref(resp.get("schema") or {})
        if ref:
            try:
                int(code)
            except ValueError:
                continue
            out[str(code)] = ref
    return out


def _schema_ref(schema: dict) -> str:
    if not isinstance(schema, dict):
        return ""
    if "$ref" in schema:
        return str(schema["$ref"])
    # Arrays referencing a schema: { type: array, items: { $ref: ... } }
    items = schema.get("items")
    if isinstance(items, dict) and "$ref" in items:
        return str(items["$ref"])
    return ""


def _short_name(ref: str) -> str:
    """Return the last segment of an OpenAPI ref."""
    if not ref:
        return ""
    return ref.rsplit("/", 1)[-1]
