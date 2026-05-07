# Skill: review-pr

Review a pull request using the company-brain structural + semantic graph.
Produces a structured review covering blast radius, business context, and risk.

## Token-efficiency rules
- Call structural tools BEFORE semantic tools — structural answers are free.
- Stop after `get_review_context` if context is sufficient. Do NOT call `get_business_context` for every node.
- Skip nodes with depth > 2 and risk_score < 0.3 unless the user asks for them.
- Summarise affected nodes as a table, not a list of paragraphs.

## Workflow

```
1. get_minimal_context(workspace_id)
   → orient: node counts, top hubs, active flows

2. detect_changes(workspace_id, since_sha?)
   → identify changed nodes, sorted by risk_score desc
   → pick the top 3 by risk for deeper analysis

3. get_impact_radius(workspace_id, node_id, direction="BOTH")
   → for each of the top-3 risky changed nodes
   → note: forward = downstream dependents; reverse = upstream callers

4. get_review_context(workspace_id, node_ids=[...all affected...])
   → one call covers all affected nodes
   → check contextBundle for business rules, risk flags, data writes

5. [conditional] get_business_context(workspace_id, node_id)
   → ONLY if a node in the review context has a security/payment risk flag
     OR the user explicitly asks about business logic

6. [conditional] list_flows + get_flow
   → ONLY if affected nodes appear in a critical flow (criticality > 0.5)
```

## Output format

### PR Review: {pr_title}

**Changed nodes** (top risk first):
| Node | Risk | Downstream affected | Upstream callers | Flag |
|------|------|--------------------|--------------------|------|
| … | 0.84 | 12 | 3 | security |

**Business impact**: {one paragraph}

**Risk summary**:
- 🔴 Critical (≥0.8): {list}
- 🟠 High (0.6–0.8): {list}
- 🟡 Medium (0.4–0.6): {list}

**Recommendation**: {approve / request-changes / needs-discussion}
