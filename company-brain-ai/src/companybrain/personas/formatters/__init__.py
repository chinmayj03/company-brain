"""
Persona formatters — ADR-0079 M4.

Factory function returns the correct formatter for a given persona.
"""
from __future__ import annotations

from typing import Optional

from companybrain.personas.formatters.base import AnswerBlock, BaseFormatter, FormattedAnswer
from companybrain.personas.formatters.developer import DeveloperFormatter
from companybrain.personas.formatters.pm import PMFormatter
from companybrain.personas.formatters.vp_eng import VPEngFormatter


_FORMATTER_REGISTRY: dict[str, type[BaseFormatter]] = {
    "dev":    DeveloperFormatter,
    "pm":     PMFormatter,
    "vp_eng": VPEngFormatter,
}


def get_formatter(persona: str) -> BaseFormatter:
    """
    Return the formatter for the given persona.
    Falls back to DeveloperFormatter if the persona is unknown.
    """
    cls = _FORMATTER_REGISTRY.get(persona, DeveloperFormatter)
    return cls()


def list_supported_personas() -> list[str]:
    """Return the list of personas that have registered formatters."""
    return list(_FORMATTER_REGISTRY.keys())


__all__ = [
    "AnswerBlock",
    "BaseFormatter",
    "FormattedAnswer",
    "DeveloperFormatter",
    "PMFormatter",
    "VPEngFormatter",
    "get_formatter",
    "list_supported_personas",
]
