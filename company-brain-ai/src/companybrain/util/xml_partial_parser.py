"""Robust partial XML parser using stdlib xml.etree.ElementTree.iterparse.

Replaces the char-by-char _recover_truncated_entities scanner shipped
in ADR-0047. iterparse emits 'end' events for each element as it CLOSES,
so a truncated XML stream yields all complete elements and silently
drops the partial trailing one.
"""
import xml.etree.ElementTree as ET
from io import StringIO


def parse_complete_elements(raw: str, element_tag: str) -> list[ET.Element]:
    """Return every <element_tag>...</element_tag> that completed before
    the truncation point. Returns [] on totally unparseable input."""
    # Wrap in a synthetic root in case the LLM emitted a fragment.
    wrapped = f"<__root__>{raw}</__root__>"
    out: list[ET.Element] = []
    try:
        for event, elem in ET.iterparse(StringIO(wrapped), events=("end",)):
            if elem.tag == element_tag:
                out.append(elem)
    except ET.ParseError:
        # iterparse raised at the truncation point — but events fired
        # for everything before it, so `out` is already correct.
        pass
    return out
