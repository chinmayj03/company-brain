"""
Turn a QueryResponse into a single markdown string for clients that want one blob.

Populates ``QueryResponse.raw_markdown`` in-place and returns the string.
"""
from __future__ import annotations

from companybrain.models.query_response import QueryResponse

_RISK_EMOJI = {"high": "🔴", "medium": "🟡", "low": "🟢"}
_CONF_EMOJI = {"high": "✅", "medium": "⚠️", "low": "❌"}


def render_to_markdown(response: QueryResponse) -> str:
    parts: list[str] = []

    parts.append(f"## Summary\n\n{response.summary}\n")

    if response.call_chain:
        parts.append("## Call Chain\n")
        for step in sorted(response.call_chain, key=lambda s: s.ord):
            edge = f"← `{step.edge_in}` " if step.edge_in else ""
            annotations = (
                " " + " ".join(f"`@{a}`" for a in step.annotations)
                if step.annotations else ""
            )
            parts.append(
                f"{step.ord}. **[{step.name}]({step.urn})**{annotations}  \n"
                f"   {edge}_{step.role}_ — {step.one_liner}\n"
            )

    if response.sql_quotes:
        parts.append("## SQL / Queries\n")
        for block in response.sql_quotes:
            lang = block.language if block.language != "other" else ""
            parts.append(
                f"**Source:** [{block.source_urn}]({block.source_urn})\n"
                f"```{lang}\n{block.body}\n```\n"
            )

    if response.affected_entities:
        parts.append("## Affected Entities\n")
        for c in response.affected_entities:
            conf_pct = f"{c.confidence * 100:.0f}%"
            parts.append(
                f"- **[{c.name}]({c.urn})** ({conf_pct}) — {c.why_relevant}\n"
            )

    if response.change_risk:
        r = response.change_risk
        emoji = _RISK_EMOJI.get(r.level, "")
        parts.append(
            f"## Change Risk {emoji} {r.level.upper()}\n\n"
            f"{r.reason}  \n"
            f"**Blast radius:** {r.blast_radius_count} downstream nodes\n"
        )
        if r.sample_affected:
            parts.append("**Sample affected:**\n")
            for c in r.sample_affected[:5]:
                parts.append(f"- [{c.name}]({c.urn}) — {c.why_relevant}\n")

    conf = response.confidence
    emoji = _CONF_EMOJI.get(conf.level, "")
    parts.append(
        f"## Confidence {emoji} {conf.level.upper()}\n\n{conf.rationale}\n"
    )

    if response.caveats:
        parts.append("## Caveats\n")
        for c in response.caveats:
            parts.append(f"- {c}\n")

    if response.follow_up_questions:
        parts.append("## Next Questions\n")
        for q in response.follow_up_questions:
            parts.append(f"- {q}\n")

    md = "\n".join(parts)
    response.raw_markdown = md
    return md
