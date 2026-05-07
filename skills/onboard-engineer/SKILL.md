# Skill: onboard-engineer

Onboard a new engineer to a codebase or service area using the structural graph.
Produces a layered orientation: big picture → entry-points → key nodes → gotchas.

## Token-efficiency rules
- Start with `get_minimal_context` — it gives orientation in <100 tokens.
- Do NOT enumerate every node. Focus on top 5 hubs + top 3 flows.
- Call `get_business_context` only for nodes the engineer explicitly asks about.
- Keep the output scannable: tables and short bullets, not paragraphs.

## Workflow

```
1. get_minimal_context(workspace_id, task_keywords=[area of interest])
   → dominant language, node count, top hubs, active flows

2. find_hubs(workspace_id, top_n=10)
   → the 10 most connected nodes — these are "must know" nodes
   → explain each hub's role in 1 sentence

3. list_flows(workspace_id, min_criticality=0.3)
   → active execution flows sorted by criticality
   → pick top 3 to trace

4. get_flow(workspace_id, flow_id)
   → for each of the top 3 flows: show the full call path
   → annotate entry-point, key decision points, exit

5. find_bridges(workspace_id, top_n=5)
   → structural chokepoints — "be careful touching these"

6. [on-demand] get_business_context(workspace_id, node_id)
   → when the engineer asks "what does X do?"
```

## Output format

### Onboarding Guide: {service / area}

**Big picture**: {2–3 sentences from get_minimal_context}

**Must-know nodes** (top hubs):
| Node | Type | Connected to | Role |
|------|------|-------------|------|

**Key execution flows**:
1. **{flow_name}** (criticality {score}): entry → … → exit
2. …

**Danger zones** (bridges — change carefully):
- {node}: sits between {community A} and {community B}

**Good starting points**: {3 nodes that are well-documented and low-risk}
