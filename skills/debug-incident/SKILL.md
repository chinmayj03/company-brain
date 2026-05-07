# Skill: debug-incident

Investigate a production incident by tracing which code changed, what it affects,
and which execution flows are impacted.

## Token-efficiency rules
- `detect_changes` is always first — it answers "what changed?" for free.
- Use risk_score to triage: only go deep on nodes with risk_score > 0.5.
- `get_impact_radius(direction="BOTH")` gives full blast radius in one call.
- Do NOT fetch business context unless the incident involves a business rule.
- Time-box investigation: if the root cause is clear after step 3, stop.

## Workflow

```
1. detect_changes(workspace_id, since_sha?)
   → what changed since the last known-good deploy?
   → sort by risk_score desc; the top entry is the prime suspect

2. get_impact_radius(workspace_id, node_id=<suspect>, direction="BOTH")
   → downstream: what did this change break?
   → upstream: what code paths lead into it?

3. list_flows(workspace_id, min_criticality=0.4)
   → which critical flows pass through the affected area?
   → prioritise flows touching payment / auth / data write nodes

4. get_flow(workspace_id, flow_id)
   → trace the affected flow end-to-end
   → find where the failure path diverges from expected behaviour

5. query_graph(workspace_id, node_id=<suspect>, relation="callers_of", depth=3)
   → find all call paths that trigger the suspect node
   → narrow to the call path active during the incident window

6. [conditional] get_business_context(workspace_id, node_id)
   → only if the node involves business logic (payment, auth, state machine)
```

## Output format

### Incident Investigation: {incident_id / description}

**Prime suspect**: `{node}` — risk_score {score}, changed {N} commits ago

**Blast radius**:
- Downstream: {count} nodes affected (key ones: {list})
- Upstream call paths: {count} entry-points reach this node

**Affected flows**: {list with criticality scores}

**Root cause hypothesis**: {1 paragraph}

**Remediation options**:
1. {immediate fix}
2. {longer-term fix}
