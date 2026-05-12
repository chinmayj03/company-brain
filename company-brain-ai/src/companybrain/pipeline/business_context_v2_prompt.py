"""
ADR-0060 — Stage 3 (ContextSynthesizer) prompt for the v2 BusinessContext schema.

This module owns the system prompt and the few-shot composer for v2 synthesis.
It is built so the heavy text (rubric + 30 worked examples) is *constant* and
therefore eligible for prompt-cache hits — the only thing that varies per
batch is the user content composed by ContextSynthesizer itself.

Public API:
    build_system_prompt() -> str
        Returns the full system prompt (rubric + library) ready to send.
        Identical across calls so the LLM provider can cache it.

    HARD_RULES_RUBRIC: str
        The rubric block alone, for unit tests that want to assert wording
        without depending on the library contents.
"""

from __future__ import annotations

from companybrain.pipeline.few_shot_library import render_for_prompt


# Twelve named anti-patterns the rubric calls out by name. Keeping them in
# one tuple lets tests assert "these are the codified twelve" without
# reading the prompt text.
ANTI_PATTERN_CATALOG: tuple[str, ...] = (
    "potential_n_plus_1",
    "literal_should_use_constant",
    "unchecked_dereference",
    "broad_exception_catch",
    "mutates_input_argument",
    "unbounded_recursion",
    "serial_remote_calls",
    "swallowed_exception",
    "missing_idempotency_key",
    "unsafe_string_concatenation_sql",
    "blocking_call_in_async_path",
    "leaking_resource",
)


HARD_RULES_RUBRIC = """\
━━━ v2 FIELD RUBRIC — HARD RULES ━━━

is_idempotent (bool | null)
  TRUE  if the body contains NO INSERT/UPDATE/DELETE/UPSERT and no remote write.
  FALSE if ANY mutation reaches a store, queue, or external system.
  NULL  ONLY when the body is not visible enough to decide.

null_handling (map<param_name, mode>)
  Populate one entry PER PARAMETER name. Modes:
    checked    — explicit if-null branch handles the null
    throws     — if-null path throws (NPE, IllegalArgumentException, etc.)
    tolerates  — value flows to a callee that handles null
    unchecked  — NPE risk; no null handling at this level
  Do NOT invent parameters. If the signature isn't visible, return {}.

transaction_mode ("read_only" | "read_write" | "no_transaction" | null)
  Extract from @Transactional/@Tx-equivalent.
  Repository SELECT-only methods default to "read_only" even when
  unannotated. Pure functions and DTO setters → "no_transaction".

anti_patterns (list<string>)
  Tag with one or more of the named catalog (twelve patterns: see below).
  Do not invent new tags — only the catalog values.
  Catalog: """ + ", ".join(ANTI_PATTERN_CATALOG) + """.

engineering_notes (list<string>)
  Short, concrete observations that a reviewer would write in a PR.
  Examples: "LATERAL unnest references outer col", "pessimistic row lock",
  "cache-aside on Caffeine". Skip generic remarks.

performance_class ("O(1)" | "O(log n)" | "O(n)" | "O(n log n)" | "O(n²)" | "unbounded" | null)
  O(n) is reserved for a CONFIRMED loop over a user-controlled collection
  or an unbounded query result. When in doubt use NULL — never guess.

security_class ("public" | "authenticated" | "authorised" | "internal_only" | "admin_only" | null)
  Extract from @PreAuthorize / @RolesAllowed / @PermitAll / filter chain.
  Default to "authenticated" if no annotation but the controller is behind
  the auth filter. NULL when the surface is not an external entry point.
"""


_OUTPUT_CONTRACT = """\
━━━ OUTPUT CONTRACT ━━━

Return a single compact JSON object containing the v1 narrative fields
AND the seven v2 fields above. Set schema_version=2. No prose, no
markdown — just the JSON object.

{
  "schema_version": 2,
  "purpose": "<1–2 sentences>",
  "history_summary": "<2–3 sentences>",
  "invariants": [...],
  "change_risk": "LOW|MEDIUM|HIGH",
  "change_risk_reason": "...",
  "owner_team": null,
  "external_dependencies": [...],
  "source_confidence": "high|medium|low",
  "gaps": [...],
  "is_idempotent": true|false|null,
  "null_handling": {"<param>": "<mode>"},
  "transaction_mode": "...",
  "anti_patterns": [...],
  "engineering_notes": [...],
  "performance_class": "...",
  "security_class": "..."
}
"""


_ROLE_HEADER = """\
You are a senior software engineer building institutional memory for a
production codebase. For each entity, populate the 28-field BusinessContext
v2 schema. Precision matters more than completeness — leave a field NULL
rather than guessing.
"""


def build_system_prompt() -> str:
    """Return the fully assembled v2 system prompt.

    Deterministic across calls so the LLM provider can cache it.
    The 30 few-shot examples are appended as compact JSON lines so the model
    can scan them like a lookup table.
    """
    return (
        _ROLE_HEADER
        + "\n"
        + HARD_RULES_RUBRIC
        + "\n"
        + _OUTPUT_CONTRACT
        + "\n━━━ FEW-SHOT LIBRARY (30 anchors) ━━━\n"
        + render_for_prompt()
        + "\n"
    )
