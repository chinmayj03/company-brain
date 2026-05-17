"""
Glossary prompt loader.

Formats the active glossary for injection into LLM prompt context.
"""

from __future__ import annotations

from companybrain.workspace.tuning_store import WorkspaceTuningStore


def format_glossary_block(terms: list[dict], max_terms: int = 20) -> str:
    """Format active glossary terms as a prompt context block.

    Args:
        terms: List of term dicts with keys: term, definition, aliases, ...
        max_terms: Cap on how many terms to include (ranked by occurrence count
                   if the list was already sorted; otherwise taken as-is).

    Returns:
        A Markdown-formatted string ready for prompt injection, or "" if empty.
    """
    if not terms:
        return ""

    lines = ["## Domain Glossary"]
    for t in terms[:max_terms]:
        defn = t.get("definition") or ""
        aliases = t.get("aliases") or []
        alias_str = f" (also: {', '.join(aliases)})" if aliases else ""
        if defn:
            lines.append(f"- **{t['term']}**{alias_str}: {defn}")
        else:
            lines.append(f"- **{t['term']}**{alias_str}")

    return "\n".join(lines)


def get_glossary_context(
    workspace_id: str,
    store: WorkspaceTuningStore,
    max_terms: int = 20,
) -> str:
    """Get formatted glossary block for prompt injection.

    Returns empty string if no glossary terms are stored or the list is empty.
    """
    terms = store.get(workspace_id, "glossary", [])
    return format_glossary_block(terms, max_terms)
