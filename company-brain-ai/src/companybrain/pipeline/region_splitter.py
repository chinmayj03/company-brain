"""Tree-sitter-based region splitter for oversized methods (ADR-0050 M3b).

For methods that don't fit even in a solo call (e.g. a single 500-line
method with inline SQL), split into AST regions: try/catch blocks,
loops, switch arms, conditional branches. Each region becomes a
separate extraction call.

ADR-0049 dependency note: AstCache lands in ADR-0049. When that ADR is
not yet merged, this module falls through to a line-based splitter so
the M3b path still fires (just without tree-sitter caching).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RegionChunk:
    parent_qname: str
    body: str
    kind: str       # 'try_statement' | 'for_statement' | ...
    file_path: str
    language: str


_REGION_TYPES = {
    "try_statement", "for_statement", "while_statement",
    "if_statement", "switch_statement", "block",
}
_MIN_REGION_BYTES = 200    # don't split into trivial pieces


def split_method_into_regions(chunk) -> list[RegionChunk]:
    """Split a large method chunk into AST regions.

    Uses AstCache (ADR-0049) when available; falls back to line-based
    splitting when tree-sitter isn't loaded.
    """
    body: str = getattr(chunk, "body", "") or ""
    if not body:
        return []

    try:
        return _split_via_treesitter(chunk, body)
    except Exception:
        # Graceful fallback: split into N equal-sized line windows.
        return _split_line_based(chunk, body)


def _split_via_treesitter(chunk, body: str) -> list[RegionChunk]:
    """Tree-sitter path — requires AstCache from ADR-0049."""
    from companybrain.util.ast_cache import AstCache  # ADR-0049 dependency
    cache = AstCache()
    language = getattr(chunk, "language", "java") or "java"
    body_hash = getattr(chunk, "body_hash", None)
    file_path = getattr(chunk, "file_path", "") or ""
    cache_key = (file_path, body_hash) if body_hash else None

    parser = _get_parser(language)
    tree = cache.parse(parser, body.encode(), cache_key)

    regions: list[RegionChunk] = []

    def walk(node):
        if (node.type in _REGION_TYPES
                and node.end_byte - node.start_byte >= _MIN_REGION_BYTES):
            regions.append(RegionChunk(
                parent_qname=getattr(chunk, "qname", "") or "",
                body=body[node.start_byte:node.end_byte],
                kind=node.type,
                file_path=file_path,
                language=language,
            ))
            return  # don't recurse into already-emitted regions
        for child in node.children:
            walk(child)

    walk(tree.root_node)
    return regions


def _split_line_based(chunk, body: str) -> list[RegionChunk]:
    """Fallback: split body into ~200-line windows."""
    lines = body.splitlines(keepends=True)
    if len(lines) < 40:
        return []

    window = 200
    language = getattr(chunk, "language", "java") or "java"
    file_path = getattr(chunk, "file_path", "") or ""
    qname = getattr(chunk, "qname", "") or ""

    regions: list[RegionChunk] = []
    for i in range(0, len(lines), window):
        segment = "".join(lines[i: i + window])
        if len(segment.encode()) >= _MIN_REGION_BYTES:
            regions.append(RegionChunk(
                parent_qname=qname,
                body=segment,
                kind="block",
                file_path=file_path,
                language=language,
            ))
    return regions


def _get_parser(language: str):
    """Return the tree-sitter parser for a language, or raise ImportError."""
    import tree_sitter_languages  # type: ignore[import]
    return tree_sitter_languages.get_parser(language)
