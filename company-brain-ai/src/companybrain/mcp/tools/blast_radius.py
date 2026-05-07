"""brain_blast_radius — BFS over Neo4j returning the impact set."""
import json
import os

from neo4j import AsyncGraphDatabase

from companybrain.mcp.tools import Tool, register


_SCHEMA = {
    "name": "brain_blast_radius",
    "description": (
        "Compute the blast radius of an entity — what would be affected if it "
        "changed (upstream) or what it depends on (downstream). Returns up to 50 "
        "neighbour URNs grouped by direction."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {
            "entity_id": {"type": "string"},
            "hops":      {"type": "integer", "default": 2},
            "direction": {"type": "string", "enum": ["upstream", "downstream", "both"], "default": "both"},
        },
        "required": ["entity_id"],
    },
}


async def _handle(args: dict) -> str:
    urn = args["entity_id"]
    hops = int(args.get("hops", 2))
    direction = args.get("direction", "both")
    clause = {
        "upstream":   f"<-[*1..{hops}]-",
        "downstream": f"-[*1..{hops}]->",
        "both":       f"-[*1..{hops}]-",
    }[direction]
    driver = AsyncGraphDatabase.driver(
        os.getenv("NEO4J_URI", "bolt://localhost:7687"),
        auth=(os.getenv("NEO4J_USER", "neo4j"),
              os.getenv("NEO4J_PASSWORD", "password")),
    )
    try:
        async with driver.session() as session:
            result = await session.run(
                f"MATCH (n {{id: $urn}}){clause}(m) "
                f"RETURN DISTINCT m.id AS id, labels(m) AS labels LIMIT 50",
                urn=urn,
            )
            rows = await result.data()
    finally:
        await driver.close()

    if not rows:
        return f"(no neighbours for {urn})"
    lines = [f"Blast radius for {urn} ({direction}, {hops} hops):"]
    for r in rows:
        lbl = r["labels"][0] if r["labels"] else "?"
        lines.append(f"  {lbl:18s}  {r['id']}")
    return "\n".join(lines)


register(Tool(
    name=_SCHEMA["name"],
    description=_SCHEMA["description"],
    schema=_SCHEMA,
    handler=_handle,
))
