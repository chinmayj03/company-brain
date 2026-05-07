# Skill: impact-analysis

Assess the full impact of a planned change before making it.
Answers: what will break, who is affected, what flows are disrupted, what tests to run.

## Token-efficiency rules
- `get_impact_radius(direction="BOTH")` is the primary tool — use it first.
- Only call `get_review_context` for nodes with depth ≤ 2 AND risk_score > 0.4.
- Skip `get_business_context` unless the changed node has a security or payment flag.
- Report impacted test nodes separately — they tell you what to run, not what will break.

## Workflow

```
1. get_minimal_context(workspace_id)
   → baseline: understand scale before predicting impact

2. get_impact_radius(workspace_id, node_id=<target>, direction="BOTH")
   → FORWARD: who depends on the node being changed?
   → REVERSE: what does it depend on that might constrain the change?
   → separate results into: depth-1 (direct), depth-2 (transitive), depth-3+

3. list_flows(workspace_id, min_criticality=0.3)
   → do any active flows pass through the target node?
   → if yes: the change affects an execution path, not just a module

4. get_flow(workspace_id, flow_id)
   → for each affected flow: show the full path
   → find where the changed node sits in the flow (early = higher impact)

5. find_hubs(workspace_id, top_n=5)
   → is the target node a hub? If so, the blast radius likely underestimates impact
   → cross-check: does the target node appear in the top-5 hub list?

6. get_review_context(workspace_id, node_ids=[...depth-1 affected...])
   → get business context + risk flags for directly affected nodes only

7. query_graph(workspace_id, node_id=<target>, relation="imported_by")
   → which modules import the changed node?
   → these will need recompilation / retesting even if not in the CALLS graph
```

## Output format

### Impact Analysis: `{node}`

**Direct impact** (depth 1): {count} nodes
| Node | Owner | Risk | Flow membership |
|------|-------|------|-----------------|

**Transitive impact** (depth 2–5): {count} nodes — {summary}

**Affected flows** ({count}):
- **{flow_name}** (criticality {score}): target at position {N}/{total}

**Recommended test scope**:
- Unit: {list of directly tested nodes}
- Integration: {list of affected flows / entry-points}
- Regression: {nodes with risk_score > 0.6}

**Safe to change**: {yes / no / conditional} — {1-sentence reasoning}
