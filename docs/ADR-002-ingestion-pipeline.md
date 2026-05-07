# ADR-002: Ingestion Pipeline — Synchronous API vs Event Queue

**Status:** Accepted  
**Date:** 2026-04-28  
**Deciders:** Founding engineering team

---

## Context

The metadata agent runs in the customer's infrastructure and sends batches of graph events (node upserts, edge observations, context metadata) to the platform. We need to decide how the platform receives and processes these events.

The core tension is: the agent sends bursts (a Git sync might produce 5,000 events at once), and graph building is CPU-intensive (confidence scoring, cycle detection, cache invalidation). Processing events synchronously in the HTTP request would either slow the agent or require massive compute to keep up.

Additionally, the ingestion pipeline must be resilient. If the graph builder crashes mid-batch, events should not be lost.

---

## Options Considered

### Option A: Synchronous HTTP Processing

Agent POSTs events → API server processes them inline → returns 200 when graph is updated.

| Dimension | Assessment |
|---|---|
| Latency | Slow for large batches (agent waits for full processing) |
| Reliability | Poor — crash during processing loses the batch |
| Simplicity | High — no queue infrastructure |
| Backpressure | Natural (agent slows down if API is slow) |
| Cost | Low — no queue service |

**Acceptable for:** MVP only. When there is one agent sending small batches, this works.

**Not acceptable for:** Multiple agents, large batch sizes, or production use.

---

### Option B: Event Queue (AWS SQS or Kafka)

Agent POSTs events → API server validates and enqueues → returns 202 Accepted → graph builder workers consume from queue asynchronously.

| Dimension | Assessment |
|---|---|
| Latency | Agent gets 202 immediately; graph update is async (seconds delay) |
| Reliability | High — queue persists events if graph builder crashes |
| Simplicity | Medium — need to operate a queue service |
| Backpressure | Queue absorbs bursts; workers scale independently |
| Cost | Low (SQS) to Medium (Kafka) |

**SQS:** Simple, cheap, managed, no ops burden. Max message size 256KB (batch multiple events). At least-once delivery (idempotent processing required). Good enough for Phase 2.

**Kafka:** Higher throughput, ordered delivery, event replay possible without a separate event log. Heavier to operate. Better for Phase 3 when we need stream processing (e.g., real-time confidence updates).

---

### Option C: Webhook-style Push from Platform

Instead of agent pushing, platform pulls from the customer's infrastructure on a schedule.

| Dimension | Assessment |
|---|---|
| Security | Good — platform needs no inbound ports on customer infra |
| Complexity | High — platform needs to manage connection credentials for every customer |
| Firewall friendliness | Better (outbound only from customer) vs. Option B (outbound only) |
| Reliability | Platform controls retry logic |

**Rejected:** Requires the platform to maintain long-lived credentials to every customer's internal systems. This is a larger security surface than the agent model and is harder to reason about at scale.

---

## Decision

**Use AWS SQS for Phase 2. Keep Phase 1 synchronous.**

For Phase 1 (MVP, public repos, no agent), there is no ingestion pipeline — the platform fetches from public GitHub APIs on demand. No queue needed.

For Phase 2 (agent-based ingestion), use SQS with a standard queue per region. The agent POSTs to the ingestion API; the API validates the HMAC signature and batch structure, then enqueues to SQS. Graph builder workers run as a separate service consuming from SQS.

The SQS approach was chosen over Kafka because:
- SQS is fully managed — no brokers to operate
- At Phase 2 throughput (hundreds of agents, not thousands), SQS is more than sufficient
- The edge_events table serves as the immutable event log for replay (so Kafka's replay advantage is not needed)
- SQS cost is negligible at this scale

Kafka should be reconsidered when real-time confidence updates (streaming the edge graph as it changes) become a product requirement.

---

## Processing Architecture

```
Agent
  │
  │ POST /v1/ingest  (HMAC-signed, max 1MB batch)
  ▼
Ingestion API (stateless, horizontally scalable)
  ├─ Verify HMAC signature
  ├─ Validate event schema
  ├─ Rate limit (per workspace: 100 req/min, 10MB/min)
  ├─ Enqueue to SQS
  └─ Return 202 Accepted

SQS Standard Queue (per region)
  ├─ Message retention: 14 days
  ├─ Visibility timeout: 60 seconds
  └─ Dead letter queue: after 3 failed processing attempts

Graph Builder Workers (auto-scaled ECS tasks or Lambda)
  ├─ Poll SQS (long-polling, 20 second wait)
  ├─ Deserialize batch
  ├─ For each event:
  │   ├─ Upsert node (ON CONFLICT DO UPDATE)
  │   ├─ Upsert edge (update last_seen, confidence)
  │   ├─ Write to edge_events log
  │   └─ Collect affected node IDs for cache invalidation
  ├─ Batch-invalidate Redis cache for all affected nodes
  └─ Acknowledge SQS message (delete from queue)
```

---

## Idempotency

Because SQS delivers at-least-once, the graph builder must be idempotent. The same event processed twice must produce the same graph state.

This is achieved by design:
- Node upserts use `ON CONFLICT DO UPDATE` — re-processing is a no-op if nothing changed
- Edge upserts use `ON CONFLICT DO UPDATE SET last_seen = NOW()` — updates the timestamp, harmless if seen twice
- edge_events log uses a `source_event_id` from the agent payload for deduplication

---

## Consequences

**What becomes easier:**
- Agent is decoupled from graph builder — agent health is independent of processing speed
- Bursts (large git syncs, first-time workspace indexing) are absorbed without impacting query latency
- Graph builder can be scaled independently of the ingestion API
- If graph builder has a bug, events sit in SQS and can be reprocessed after the bug is fixed

**What becomes harder:**
- Graph updates are eventually consistent (seconds of lag after agent sends events)
- Dead letter queue requires monitoring and alerting
- Debugging ingestion failures requires tracing across API → SQS → Worker

**What we will need to revisit:**
- Migrate from SQS to Kafka when real-time graph streaming is a product requirement (Phase 3)
- Add a dedicated workspace-level SQS queue if noisy tenants impact processing latency for others

---

## Action Items

1. [ ] Implement ingestion API with HMAC signature verification
2. [ ] Set up SQS queue with DLQ and 14-day retention
3. [ ] Implement graph builder worker with idempotent upsert logic
4. [ ] Add DLQ depth alarm in CloudWatch (alert if DLQ > 10 messages)
5. [ ] Load test: simulate 50 agents sending 1,000 events/batch simultaneously, measure SQS lag
