"""
GraphQL SDL schema extractor (S5) — ADR-0058.

Uses ``graphql-core`` to parse ``.graphqls`` / ``.graphql`` / ``.gql`` files
into a typed AST, then emits GraphQLType / GraphQLField / GraphQLQuery
entities. Resolver-implementation edges (``RESOLVES``) are filled in by
``schema_resolver`` once code-side resolver methods have been seen.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from companybrain.extractors.base import Extractor
from companybrain.models.entities import (
    ExtractedBatch,
    GraphQLField,
    GraphQLQuery,
    GraphQLType,
    SchemaExtractedBatch,
)

try:
    from graphql import parse as gql_parse
    from graphql.language import ast as gql_ast
    _GRAPHQL_OK = True
except ImportError:  # pragma: no cover
    _GRAPHQL_OK = False


_OPERATION_TYPE_NAMES = {"Query", "Mutation", "Subscription"}


class GraphQLExtractor:
    """Universal-extraction Extractor for GraphQL SDL files."""

    kind = "schema_graphql"

    def supports(self, path: Path) -> bool:
        return path.suffix.lower() in {".graphql", ".graphqls", ".gql"}

    def extract(self, path: Path, content: str, *, repo: str = "") -> ExtractedBatch:
        batch = SchemaExtractedBatch(file=str(path), repo=repo, extractor_kind=self.kind)
        if not _GRAPHQL_OK:
            out = batch.to_extracted_batch()
            setattr(out, "_schema_batch", batch)
            return out

        try:
            document = gql_parse(content)
        except Exception:
            out = batch.to_extracted_batch()
            setattr(out, "_schema_batch", batch)
            return out

        for defn in document.definitions:
            _handle_definition(defn, batch, str(path), repo)

        out = batch.to_extracted_batch()
        setattr(out, "_schema_batch", batch)
        return out


def _handle_definition(defn, batch: SchemaExtractedBatch, source_file: str, repo: str) -> None:
    name = getattr(getattr(defn, "name", None), "value", None)
    if name is None:
        return

    kind = _node_kind(defn)
    if kind is None:
        return

    # Handle scalar / enum / union — emit a GraphQLType but no field children.
    if kind in {"ENUM", "SCALAR", "UNION", "INPUT_OBJECT", "INTERFACE", "OBJECT"}:
        fields_raw = getattr(defn, "fields", None) or []
        field_summary: list[dict] = []
        for f in fields_raw:
            fname = getattr(getattr(f, "name", None), "value", None)
            if fname is None:
                continue
            ftype = _type_to_str(getattr(f, "type", None))
            args = _arguments(getattr(f, "arguments", None))
            field_summary.append({"name": fname, "type": ftype, "args": args})

        type_obj = GraphQLType(
            name=name,
            kind=kind,
            fields=field_summary,
            source_file=source_file,
            repo=repo,
        )
        batch.gql_types.append(type_obj)

        if kind in {"OBJECT", "INTERFACE", "INPUT_OBJECT"}:
            for f in fields_raw:
                fname = getattr(getattr(f, "name", None), "value", None)
                if fname is None:
                    continue
                field = GraphQLField(
                    name=fname,
                    parent_type_urn=type_obj.external_id,
                    type=_type_to_str(getattr(f, "type", None)),
                    args=_arguments(getattr(f, "arguments", None)),
                    source_file=source_file,
                    repo=repo,
                )
                batch.gql_fields.append(field)

                # Promote Query / Mutation / Subscription fields to GraphQLQuery.
                if name in _OPERATION_TYPE_NAMES:
                    batch.gql_ops.append(GraphQLQuery(
                        name=fname,
                        operation=name.lower(),
                        return_type=field.type,
                        args=field.args,
                        source_file=source_file,
                        repo=repo,
                    ))


def _node_kind(defn) -> Optional[str]:
    """Map graphql-core AST node to our normalised kind string.

    ``graphql-core`` 3.x suffixes class names with ``Node``; older 2.x
    omitted it. Match both so we don't get a silent miss on a major-version
    bump.
    """
    cls = type(defn).__name__
    # Tolerate both with and without the trailing 'Node' suffix.
    if cls.endswith("Node"):
        cls = cls[: -len("Node")]
    mapping = {
        "ObjectTypeDefinition": "OBJECT",
        "ObjectTypeExtension": "OBJECT",
        "InterfaceTypeDefinition": "INTERFACE",
        "InterfaceTypeExtension": "INTERFACE",
        "UnionTypeDefinition": "UNION",
        "UnionTypeExtension": "UNION",
        "EnumTypeDefinition": "ENUM",
        "EnumTypeExtension": "ENUM",
        "ScalarTypeDefinition": "SCALAR",
        "ScalarTypeExtension": "SCALAR",
        "InputObjectTypeDefinition": "INPUT_OBJECT",
        "InputObjectTypeExtension": "INPUT_OBJECT",
    }
    return mapping.get(cls)


def _type_to_str(type_node) -> str:
    """Render a GraphQL type AST node back to its SDL spelling, e.g. ``[User!]!``."""
    if type_node is None:
        return ""
    cls = type_node.__class__.__name__
    if cls.endswith("Node"):
        cls = cls[: -len("Node")]
    if cls == "NonNullType":
        return _type_to_str(type_node.type) + "!"
    if cls == "ListType":
        return "[" + _type_to_str(type_node.type) + "]"
    if cls == "NamedType":
        return type_node.name.value
    return ""


def _arguments(args) -> list[dict]:
    out: list[dict] = []
    for a in args or []:
        name = getattr(getattr(a, "name", None), "value", None)
        if name is None:
            continue
        out.append({"name": name, "type": _type_to_str(getattr(a, "type", None))})
    return out
