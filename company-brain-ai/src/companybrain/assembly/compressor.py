"""Task-aware compression — drop fields not relevant to the task type.

Critical lesson: compression that strips the actual code body (`code_snippet`)
or the SQL body (`metadata.query_text`) makes the LLM blind to the only thing
that can answer 'what tables does X read'. Every task-type now KEEPS those
fields by default; compression only trims metadata that isn't useful for the
specific task.

Defaults included for every task:
  t1_summary, file, repo, signature, code_snippet (capped),
  metadata.query_text, metadata.code_snippet, metadata.javadoc,
  metadata.validation_constraints, metadata.business_context, relationships.
"""
from companybrain.assembly.types import TaskType


# Per-task EXTRA fields on top of the always-kept set below. Each task can
# also restrict relationships to a subset by specifying "relationships:TYPE".
_EXTRA_FIELDS = {
    TaskType.READ:    [],
    TaskType.WRITE:   ["metadata.state", "metadata.assumptions"],
    TaskType.DEBUG:   ["metadata.assumptions", "metadata.error_paths",
                       "metadata.failure_modes", "metadata.gaps"],
    TaskType.AUDIT:   ["metadata.severity", "metadata.compliance_tags",
                       "metadata.data_sensitivity", "metadata.audited_by"],
    TaskType.ONBOARD: ["tags", "metadata.business_capability",
                       "metadata.personas_affected"],
}

# Always-keep fields — these go into every T2 block regardless of task type.
# These are the fields the LLM NEEDS to answer code-level questions: actual
# bodies, signatures, validation, and the business context blob.
_ALWAYS_KEEP = [
    "t1_summary",
    "file", "repo", "signature",
    "metadata.props",
    "metadata.code_snippet",
    "metadata.query_text",
    "metadata.javadoc",
    "metadata.validation_constraints",
    "metadata.business_context",
    "metadata.signature",
    "metadata.confidence",
    "metadata.last_modified_commit",
    "metadata.first_appeared_commit",
    "relationships",     # always keep all edge types — the LLM needs full graph context
]

# Per-field character cap so a single oversized snippet can't blow the prompt.
# code_snippet is the most expensive — bumped from default to ADR-0040 spec.
_FIELD_CAPS = {
    "metadata.code_snippet":   2000,
    "metadata.query_text":     1500,
    "metadata.javadoc":        800,
}


def _get_path(obj: dict, path: str):
    ref = obj
    for part in path.split("."):
        if ref is None:
            return None
        ref = ref.get(part) if isinstance(ref, dict) else None
    return ref


def _set_path(obj: dict, path: str, value) -> None:
    parts = path.split(".")
    ref = obj
    for part in parts[:-1]:
        ref = ref.setdefault(part, {})
        if not isinstance(ref, dict):
            return
    ref[parts[-1]] = value


def _cap(value, limit: int):
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "  …[truncated]"
    return value


def compress(t2_entry: dict, task_type: TaskType) -> dict:
    """Return a copy of the T2 entry with NON-essential fields trimmed but
    code bodies, SQL bodies, and business context PRESERVED.

    The renderer dumps `entity` as JSON inside ```json ... ``` so the LLM
    sees structured fields. The single most important lesson from previous
    iterations: never strip query_text or code_snippet — they're the only
    way the LLM can answer 'what SQL does X execute'.
    """
    e = t2_entry.get("entity") or {}
    extras = _EXTRA_FIELDS.get(task_type, [])
    spec_paths = list(_ALWAYS_KEEP) + list(extras)

    compressed: dict = {
        "entity_type":    e.get("entity_type"),
        "qualified_name": e.get("qualified_name"),
        "name":           e.get("name") or e.get("qualified_name"),
    }

    for spec in spec_paths:
        path, _, edge_filter = spec.partition(":")
        if edge_filter and path == "relationships":
            rels = [r for r in (e.get("relationships") or [])
                    if r.get("edge_type") == edge_filter]
            if rels:
                compressed["relationships"] = rels
            continue

        value = _get_path(e, path)
        if value is None or value == "" or value == [] or value == {}:
            continue

        cap = _FIELD_CAPS.get(path)
        if cap:
            value = _cap(value, cap)

        _set_path(compressed, path, value)

    return {"urn": t2_entry["urn"], "entity": compressed}
