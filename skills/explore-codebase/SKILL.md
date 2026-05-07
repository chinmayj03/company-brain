# Skill: explore-codebase

Explore an unfamiliar codebase or service area. Produces a layered structural map:
entry-points → core logic → shared utilities → data layer.

## Token-efficiency rules
- `get_minimal_context` costs near-zero tokens — always start here.
- `find_hubs` and `find_bridges` together cover 95% of orientation needs.
- Use `semantic_search_nodes` with a broad query, then narrow with node_type filter.
- Do NOT call `get_business_context` for more than 3 nodes without being asked.

## Workflow

```
1. get_minimal_context(workspace_id, task_keywords=[topic])
   → language, scale, top hubs, active flows
   → adjust depth of exploration based on node_count

2. find_hubs(workspace_id, top_n=15)
   → most-connected nodes — the "centre of gravity"
   → group by node_type to identify patterns (are hubs Services? Utils? Schemas?)

3. list_flows(workspace_id, min_criticality=0.2)
   → entry-point → core logic paths
   → gives the traversal order without needing to read source

4. get_flow(workspace_id, flow_id)
   → for the top 2 flows: trace the full execution path

5. find_bridges(workspace_id, top_n=10)
   → structural connectors between sub-systems
   → these are where interface contracts are defined

6. semantic_search_nodes(workspace_id, query="{area}", node_type="Service")
   → find service-level nodes for targeted deep-dives

7. query_graph(workspace_id, node_id, relation="imports_of", depth=2)
   → understand the dependency layering of a key node
```

## Output format

### Codebase Map: {workspace / service}

**Scale**: {node_count} nodes, {edge_count} edges, dominant language: {lang}

**Core nodes** (top hubs):
| Node | Type | Degree | Role |
|------|------|--------|------|

**Main execution paths**:
1. **{flow}**: {entry} → {key steps} → {exit}

**Subsystem boundaries** (bridges):
- `{bridge}` connects {subsystem A} ↔ {subsystem B}

**Entry-points** ({count}): {list}

**Suggested reading order** for a new contributor: {ordered list of 5 nodes}
