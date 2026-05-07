#!/usr/bin/env bash
# Best-effort cumulative Anthropic spend for THIS pipeline run, derived from
# Postgres edge_events.metadata.cost_usd (populated by ADR-0010 LLMCallRecord).
# Returns "0.00" if no telemetry yet.
set -euo pipefail
docker exec cb-postgres psql -U companybrain -d companybrain -t -A -c \
  "SELECT coalesce(round(sum((metadata->>'cost_usd')::numeric)::numeric, 4), 0)
   FROM edge_events
   WHERE occurred_at > now() - interval '1 hour'
     AND metadata ? 'cost_usd';" 2>/dev/null || echo "0.00"
