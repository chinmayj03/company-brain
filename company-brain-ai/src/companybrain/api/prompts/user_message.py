"""
Per-intent user message templates — ADR-0043 WS2.

Each template is a Python format string with placeholders:
  {context}   — the rendered SmartZone context block (may be empty)
  {question}  — the user's original question

The right template is selected by the intent router and passed to the LLM
as the user turn so the model receives intent-calibrated instructions.
"""
from __future__ import annotations

from companybrain.api.intent_router import Intent

# ── Templates ─────────────────────────────────────────────────────────────────

TEMPLATES: dict[Intent | str, str] = {

    "call_chain": """\
KNOWLEDGE BASE:
{context}

---

QUESTION: {question}

Trace the complete call chain end-to-end.
For each step, cite the exact URN in square brackets, e.g. [urn:cb:...].
Format:
  Call chain: A [urn:cb:…] → B [urn:cb:…] → C [urn:cb:…]

If the chain is incomplete in the knowledge base, say so explicitly.
""",

    "data_flow": """\
KNOWLEDGE BASE:
{context}

---

QUESTION: {question}

Identify every column / field read or written.
For each, cite the entity URN that accesses it [urn:cb:…] and quote any
SQL or query text verbatim inside backticks.
Format:
  Data flow:
    - <entity> [urn:cb:…] reads `<column>` via `<query>`
    - <entity> [urn:cb:…] writes `<column>` via `<query>`
""",

    "change_risk": """\
KNOWLEDGE BASE:
{context}

---

QUESTION: {question}

Enumerate every entity that would be affected by this change.
Cite each with its URN [urn:cb:…] and rate the risk (HIGH / MEDIUM / LOW).
Format:
  Change risk: HIGH / MEDIUM / LOW
  Reason: <one sentence>
  Affected entities:
    - <name> [urn:cb:…] — <why affected>
""",

    "concept": """\
KNOWLEDGE BASE:
{context}

---

QUESTION: {question}

Explain in 2–4 paragraphs. Cite every entity you mention with its URN
[urn:cb:…]. If the knowledge base doesn't contain enough information to
answer fully, say so and list what's missing.
""",

    "other": """\
KNOWLEDGE BASE:
{context}

---

QUESTION: {question}

Answer using ONLY information from the KNOWLEDGE BASE above.
Cite every entity with its URN [urn:cb:…].
If the knowledge base doesn't contain enough information, say so clearly.
""",
}

_NO_CONTEXT_SUFFIX = (
    "\n\nNote: No brain context is available for this workspace. "
    "Run the extraction pipeline on the repo first (`brain ingest`)."
)


def build_user_message(
    question: str,
    *,
    intent: Intent | str,
    context: str | None,
) -> str:
    """Render the per-intent user message with the assembled context.

    Falls back to the 'other' template for unknown intents.
    When context is None/empty, appends a note so the LLM knows why
    the knowledge base section is empty.
    """
    template = TEMPLATES.get(intent, TEMPLATES["other"])
    rendered_context = context or ""
    msg = template.format(context=rendered_context, question=question)
    if not rendered_context:
        msg += _NO_CONTEXT_SUFFIX
    return msg
