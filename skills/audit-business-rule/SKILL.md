# Skill: audit-business-rule

Find all code that implements, enforces, or potentially violates a stated business rule.
Returns nodes, their risk scores, and business context annotations.

## Token-efficiency rules
- Use `semantic_search_nodes` as the primary discovery tool — it is cheap.
- Do NOT call `get_business_context` for more than 5 nodes per audit.
- Batch `get_impact_radius` for multiple nodes in a single pass.
- If >10 nodes match, report the top 5 by risk_score and summarise the rest.

## Workflow

```
1. get_minimal_context(workspace_id)
   → understand the codebase structure before searching

2. semantic_search_nodes(workspace_id, query="{rule keywords}", top_k=15)
   → find nodes related to the business rule by name / purpose
   → filter to node_type IN (Function, Method, Service, API)

3. get_business_context(workspace_id, node_id)
   → for the top 5 matches by match_score
   → look for: riskFlags, businessRules, dataWrites relevant to the rule

4. get_impact_radius(workspace_id, node_id, direction="FORWARD")
   → for any node that IMPLEMENTS the rule: who depends on it?
   → surface potential violation points in the downstream graph

5. query_graph(workspace_id, node_id, relation="callers_of")
   → for rule ENFORCEMENT nodes: who can bypass by calling around them?
```

## Output format

### Business Rule Audit: "{rule}"

**Implementing nodes** ({count}):
| Node | File | Risk | Status |
|------|------|------|--------|

**Potential bypass paths** (nodes that reach implementing code without going through enforcement):
- {list}

**Gaps / violations found**: {summary or "None identified"}

**Recommended follow-up**: {specific nodes to annotate or refactor}
