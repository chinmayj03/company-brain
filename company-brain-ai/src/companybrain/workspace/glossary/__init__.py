"""Glossary auto-discovery: scan corpus, generate definitions, load into prompt context."""

from companybrain.workspace.glossary.discoverer import GlossaryCandidate, GlossaryDiscoverer
from companybrain.workspace.glossary.promoter import GlossaryPromoter
from companybrain.workspace.glossary.loader import format_glossary_block, get_glossary_context

__all__ = [
    "GlossaryCandidate",
    "GlossaryDiscoverer",
    "GlossaryPromoter",
    "format_glossary_block",
    "get_glossary_context",
]
