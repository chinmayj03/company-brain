"""Render the assembled payload as a single string in harness §6.2 format."""
import json
from companybrain.assembly.types import SmartZonePayload


def render(payload: SmartZonePayload) -> str:
    lines: list[str] = []
    lines.append("=== COMPANY BRAIN CONTEXT ===\n")
    if payload.t0:
        lines.append("[ENTITY SUMMARIES - T0]")
        for entry in payload.t0:
            lines.append(f"  {entry['urn']}\n    → {entry['t0']}")
        lines.append("")
    if payload.t1:
        lines.append("[ENTITY DETAIL - T1]")
        for entry in payload.t1:
            lines.append(f"  {entry['urn']}\n    {entry['t1']}")
        lines.append("")
    if payload.t2:
        lines.append("[FULL CONTEXT - T2]")
        for entry in payload.t2:
            lines.append(
                f"  {entry['urn']}\n```json\n{json.dumps(entry['entity'], indent=2)}\n```"
            )
        lines.append("")
    if payload.business_context:
        lines.append("[BUSINESS CONTEXT]")
        for bc in payload.business_context:
            lines.append(f"  {bc.get('qualified_name', '?')}: {bc.get('t1_summary', '')}")
        lines.append("")
    if payload.blast_radius:
        lines.append("[BLAST RADIUS]")
        for seed, neighbours in payload.blast_radius.items():
            lines.append(f"  {seed} →")
            for n in neighbours[:10]:
                lines.append(f"    {n}")
        lines.append("")
    lines.append(
        f"=== END BRAIN CONTEXT (tokens: {payload.tokens_used} / {payload.tokens_budget}) ==="
    )
    return "\n".join(lines)
