"""
Persona-Aware Query Template Framework — ADR-0079 P1.

Public API:
    from companybrain.personas import route_query, get_formatter, load_bindings

    result = route_query(question, persona_param="pm")
    formatter = get_formatter(result.persona)
    formatted = formatter.format(raw_answer, result.shape, bindings=bindings)
"""
from __future__ import annotations

from companybrain.personas.router import RouterResult, route
from companybrain.personas.formatters import FormattedAnswer, get_formatter
from companybrain.personas.templates import load_all_templates, get_shape


def route_query(
    question: str,
    persona_param: str | None = None,
) -> RouterResult:
    """
    Route a query to the best-matching persona + shape.

    Convenience wrapper around router.route() that loads templates automatically.
    """
    return route(question, persona_param=persona_param)


def load_bindings(vertical: str = "healthcare-rcm") -> dict:
    """
    Load vertical bindings for the given vertical.

    Returns an empty dict if the bindings file is not found or parse fails.
    """
    from pathlib import Path
    import yaml
    import logging

    log = logging.getLogger(__name__)

    bindings_dir = Path(__file__).parent / "bindings"
    path = bindings_dir / f"{vertical}.yaml"
    if not path.exists():
        log.debug("[personas] Bindings file not found: %s", path)
        return {}
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except Exception as exc:
        log.warning("[personas] Failed to load bindings %s: %s", path, exc)
        return {}


__all__ = [
    "route_query",
    "get_formatter",
    "load_bindings",
    "load_all_templates",
    "get_shape",
    "RouterResult",
    "FormattedAnswer",
]
