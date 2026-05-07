"""Task-aware compression — drop fields not relevant to the task type."""
from companybrain.assembly.types import TaskType


_KEEP_FIELDS = {
    TaskType.READ:    ["t1_summary", "metadata.props", "relationships:CALLS"],
    TaskType.WRITE:   ["t1_summary", "metadata.props", "metadata.state",
                       "relationships", "metadata.assumptions"],
    TaskType.DEBUG:   ["t1_summary", "metadata", "relationships",
                       "metadata.assumptions", "metadata.error_paths"],
    TaskType.AUDIT:   ["t1_summary", "relationships:RELIES_ON",
                       "relationships:CALLS", "metadata.severity"],
    TaskType.ONBOARD: ["t1_summary", "tags"],
}


def compress(t2_entry: dict, task_type: TaskType) -> dict:
    """Return a copy of the T2 entry with the entity JSON stripped to relevant fields.

    Preserves the {urn, entity} envelope so the renderer can handle T2 uniformly.
    """
    keep = _KEEP_FIELDS.get(task_type, ["t1_summary"])
    e = t2_entry["entity"]
    compressed = {
        "entity_type": e["entity_type"],
        "qualified_name": e["qualified_name"],
    }
    for spec in keep:
        path, _, edge_filter = spec.partition(":")
        if edge_filter and path == "relationships":
            compressed["relationships"] = [r for r in e.get("relationships", [])
                                            if r.get("edge_type") == edge_filter]
        else:
            ref = e
            for part in path.split("."):
                if ref is None:
                    break
                ref = ref.get(part) if isinstance(ref, dict) else None
            if ref is not None:
                compressed[path] = ref
    return {"urn": t2_entry["urn"], "entity": compressed}
