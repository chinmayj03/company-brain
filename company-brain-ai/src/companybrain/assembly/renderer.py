"""Render the assembled payload as a single string the LLM can navigate.

The previous version dumped each T2 entity as a single json.dumps blob, which
hides the most important fields (query_text, code_snippet, business_context)
inside nested structures. The new format uses explicit section headers so the
LLM scans for "## SQL", "## Body", "## Business context" the same way it
would scan a code-review comment.
"""
import json
from companybrain.assembly.types import SmartZonePayload


_MAX_SNIPPET = 2000   # mirrors compressor cap; defensive double-trim
_MAX_RELS_PER_NODE = 30


def _format_t2_entity(entry: dict) -> list[str]:
    """Render a single T2 entry with explicit per-field sections.

    Sections (only emitted when populated):
      - core identity (file / repo / signature)
      - business context (purpose / change_risk / data_sensitivity / …)
      - SQL / query body (verbatim, in ```sql fence)
      - method body / code_snippet (verbatim, in ```java fence)
      - validation constraints, javadoc, gaps
      - relationships (grouped by edge type)
    """
    e = entry.get("entity") or {}
    urn = entry.get("urn", "")
    md  = e.get("metadata") or {}
    out: list[str] = []

    # Header
    name = e.get("name") or e.get("qualified_name") or "?"
    etype = e.get("entity_type") or "?"
    out.append(f"\n### {name}  ({etype})")
    out.append(f"  urn: {urn}")
    if e.get("file"):
        out.append(f"  file: {e['file']}  · repo: {e.get('repo','?')}")
    if e.get("signature") or md.get("signature"):
        out.append(f"  signature: `{e.get('signature') or md.get('signature')}`")

    # Business context — the rich semantic block we worked hard to populate
    bc = md.get("business_context") or {}
    if bc:
        if bc.get("purpose"):
            out.append(f"\n  **Purpose:** {bc['purpose']}")
        for k in ("business_capability", "change_risk", "change_risk_reason",
                  "data_sensitivity", "deprecation_status",
                  "performance_notes", "owner_team"):
            v = bc.get(k)
            if v:
                out.append(f"  - {k}: {v}")
        for k in ("invariants", "failure_modes", "side_effects",
                  "compliance_tags", "personas_affected", "external_dependencies",
                  "blast_radius", "related_concepts", "gaps"):
            v = bc.get(k) or []
            if v:
                out.append(f"  - {k}: {', '.join(map(str, v[:8]))}")

    # SQL / query body — quote verbatim so the LLM can cite it
    qt = md.get("query_text")
    if qt:
        body = qt[:_MAX_SNIPPET]
        out.append(f"\n  **Query body:**\n```sql\n{body}\n```")

    # Method body / code snippet
    cs = md.get("code_snippet")
    if cs:
        body = cs[:_MAX_SNIPPET]
        out.append(f"\n  **Method body:**\n```java\n{body}\n```")

    if md.get("javadoc"):
        out.append(f"\n  **Javadoc:** {str(md['javadoc'])[:600]}")
    if md.get("validation_constraints"):
        out.append(f"  **Validation:** {md['validation_constraints']}")

    # Relationships grouped by edge type for at-a-glance scanning
    rels = (e.get("relationships") or [])[:_MAX_RELS_PER_NODE]
    if rels:
        by_type: dict[str, list[str]] = {}
        for r in rels:
            by_type.setdefault(r.get("edge_type", "?"), []).append(
                str(r.get("target_id") or r.get("to_entity") or "?")
            )
        out.append("\n  **Relationships:**")
        for et, targets in by_type.items():
            out.append(f"  - {et}: {', '.join(targets[:10])}"
                       + (f"  (+{len(targets)-10} more)" if len(targets) > 10 else ""))

    return out


def render(payload: SmartZonePayload) -> str:
    lines: list[str] = []
    lines.append("=== COMPANY BRAIN CONTEXT ===")
    lines.append(f"task: {payload.task!r}    task_type: {payload.task_type}")
    lines.append("")

    if payload.t2:
        lines.append("## T2 — primary nodes (read carefully; cite by name)")
        for entry in payload.t2:
            lines.extend(_format_t2_entity(entry))
        lines.append("")

    if payload.t1:
        lines.append("## T1 — nearby context (skim for relevant facts)")
        for entry in payload.t1:
            lines.append(f"  - {entry['urn']}")
            if entry.get("t1"):
                lines.append(f"      {entry['t1']}")
        lines.append("")

    if payload.t0:
        lines.append("## T0 — distant nodes (use for awareness only)")
        for entry in payload.t0:
            lines.append(f"  - {entry['urn']}: {entry.get('t0','')}")
        lines.append("")

    if payload.business_context:
        lines.append("## Business context — owner/risk/intent for primary nodes")
        for bc in payload.business_context:
            qn = bc.get('qualified_name', '?')
            summary = bc.get('t1_summary', '')
            lines.append(f"  - {qn}: {summary}")
        lines.append("")

    if payload.blast_radius:
        lines.append("## Blast radius — graph reachability from primary seeds")
        for seed, neighbours in payload.blast_radius.items():
            short_seed = seed.split(":")[-1] if ":" in seed else seed
            lines.append(f"  {short_seed} →")
            for n in neighbours[:15]:
                short_n = n.split(":")[-1] if ":" in n else n
                lines.append(f"    {short_n}")
        lines.append("")

    lines.append(
        f"=== END BRAIN CONTEXT (tokens: {payload.tokens_used} / {payload.tokens_budget}) ==="
    )
    return "\n".join(lines)
