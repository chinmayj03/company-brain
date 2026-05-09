#!/usr/bin/env bash
# Best-effort cumulative Anthropic spend for THIS pipeline run.
# Queries llm_call_log (written by ADR-0040 Tier 0.B) with fallback to the
# legacy edge_events.metadata.cost_usd path.
# Returns "0.00" if no telemetry yet.
set -euo pipefail

# Primary: llm_call_log table (ADR-0040 Tier 0.B)
SPEND=$(docker exec cb-postgres psql -U companybrain -d companybrain -t -A -c \
  "SELECT coalesce(round(sum(cost_usd)::numeric, 4), 0)
   FROM llm_call_log
   WHERE occurred_at > now() - interval '1 hour';" 2>/dev/null || echo "")

# Fallback: legacy edge_events path
if [ -z "$SPEND" ] || [ "$SPEND" = "0" ]; then
  SPEND=$(docker exec cb-postgres psql -U companybrain -d companybrain -t -A -c \
    "SELECT coalesce(round(sum((metadata->>'cost_usd')::numeric)::numeric, 4), 0)
     FROM edge_events
     WHERE occurred_at > now() - interval '1 hour'
       AND metadata ? 'cost_usd';" 2>/dev/null || echo "0.00")
fi

echo "${SPEND:-0.00}"
