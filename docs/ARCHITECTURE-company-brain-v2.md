# Company Brain — Full Architecture Design (v2)

> *"The missing layer between raw company data and reliable AI automation."*

This document describes the complete architecture for Company Brain as a universal, domain-agnostic company knowledge platform. The code dependency graph built in v1 is the first domain instance of this architecture.

---

## The Core Architectural Bet

Most "knowledge management" tools treat knowledge as **documents** — things you search and read. Company Brain treats knowledge as a **graph of executable facts** — things an AI agent can traverse, reason over, and act on.

The distinction:
- A wiki page says "our refund policy is 30 days." A knowledge graph says: *RefundPolicy --GOVERNED_BY--> L1Support --ESCALATES_TO--> L2Support --APPLIES_TO--> DigitalGoods[exception]* — and that's queryable, traversable, and blast-radius-aware.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           DATA SOURCES                                  │
│  Git repos │ Slack │ Zendesk │ Confluence │ Jira │ Email │ Databases    │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │  raw events via SQS
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                        INGESTION LAYER                                  │
│   Domain Collector → Chunker → Extraction Pipeline → Signal Fusion      │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │  structured entities + relationships
                                ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                     UNIVERSAL KNOWLEDGE GRAPH                           │
│   PostgreSQL: knowledge_nodes + knowledge_edges + knowledge_context     │
│   pgvector: semantic embeddings on nodes + context                      │
└───────────────────────────────┬─────────────────────────────────────────┘
                                │
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                  ▼
    ┌──────────────┐  ┌──────────────────┐  ┌────────────────────┐
    │  Query API   │  │  Skills File API │  │  Staleness Engine  │
    │  (NL + Graph)│  │  (agent-ready)   │  │  (decay + refresh) │
    └──────────────┘  └──────────────────┘  └────────────────────┘
              │                 │
              ▼                 ▼
    ┌──────────────┐  ┌──────────────────┐
    │  Dashboard   │  │   AI Agents      │
    │  (React)     │  │  (any agent SDK) │
    └──────────────┘  └──────────────────┘
```

---

## Layer 1: Ingestion — Domain Collector Plugin System

Every data source is a **collector** that implements the same interface. This is the key extensibility point.

### Collector Interface (Python)

```python
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import AsyncIterator

@dataclass
class RawChunk:
    source_system: str          # 'git', 'slack', 'zendesk'
    source_ref: str             # commit hash, message URL, ticket ID
    domain: str                 # 'engineering', 'support', ...
    content: str                # raw text content
    timestamp: datetime
    metadata: dict              # source-specific fields
    is_annotated: bool = False  # human-flagged (always verbatim)

class DomainCollector(ABC):
    domain: str
    source_system: str

    @abstractmethod
    async def stream_chunks(
        self,
        workspace_id: str,
        config: dict,
        since: datetime | None = None,
    ) -> AsyncIterator[RawChunk]:
        """Yield chunks incrementally. Supports resumable ingestion."""
        ...

    @abstractmethod
    async def health_check(self, config: dict) -> bool:
        """Verify credentials and connectivity."""
        ...
```

### Implemented Collectors (Roadmap)

| Collector | Domain | Source System | Key Signal |
|-----------|--------|---------------|------------|
| `GitCollector` ✅ | engineering | Git/GitHub/GitLab | commits, PRs, diffs |
| `SlackCollector` | multi-domain | Slack | decisions, incidents, escalations |
| `ZendeskCollector` | support | Zendesk | ticket resolution patterns |
| `ConfluenceCollector` | multi-domain | Confluence | policy docs, runbooks |
| `JiraCollector` | engineering/ops | Jira | incidents, sprint decisions |
| `NotionCollector` | multi-domain | Notion | company wiki, SOPs |
| `EmailCollector` | multi-domain | Gmail/Outlook | executive decisions, approvals |
| `SchemaCollector` | engineering | PostgreSQL/MySQL | DB schema + query patterns |
| `OpenTelemetryCollector` | engineering | OTEL | runtime call graphs |
| `TerraformCollector` | ops | Terraform/IaC | infra dependency graph |

Each collector is a ~200-line Python module. Adding a new domain takes one afternoon.

---

## Layer 2: LLM Extraction Pipeline — Quality at Scale

The biggest risk in any knowledge extraction system is **LLM hallucination becoming ground truth**. The pipeline is designed to prevent this at every step.

### Pass Architecture (4-Pass Extraction)

```
Raw Chunks
    │
    ▼ Pass 1: ENTITY EXTRACTION         [TaskRole.FAST]
    │  — What entities exist in this content?
    │  — Structured JSON output only, no prose
    │  — Deduplication by (domain, entity_type, name.lower())
    │
    ▼ Pass 2: RELATIONSHIP EXTRACTION   [TaskRole.BALANCED]
    │  — What relationships exist between known entities?
    │  — Validates: both endpoints must be in the known entity set
    │  — Rejects: any relationship involving an unknown entity name
    │
    ▼ Pass 3: CONTEXT SYNTHESIS         [TaskRole.SYNTHESIS]
    │  — For each entity, synthesise a 2-4 sentence business context summary
    │  — MUST cite the source ref for every claim
    │  — Annotated entities get verbatim source, not paraphrased
    │
    ▼ Pass 4: VERIFICATION + GAP DETECT [TaskRole.REASONING]
       — Second LLM critiques Pass 3 output:
         "Does any claim exceed what the source material actually says?"
       — Flags gaps: entities mentioned but not explained
       — Outputs confidence score per context entry
```

### Anti-Hallucination Guardrails

**1. Structured extraction only — no prose in Pass 1 and 2**
```python
ENTITY_SCHEMA = {
    "type": "object",
    "properties": {
        "entities": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["entity_type", "name", "source_quote"],
                "properties": {
                    "entity_type": {"type": "string"},
                    "name": {"type": "string"},
                    "source_quote": {"type": "string", "maxLength": 200},
                    # ↑ Forces grounding: every entity needs a verbatim quote from source
                }
            }
        }
    }
}
```

**2. Self-consistency sampling**
For high-stakes nodes (confidence < 0.7 or `source_count == 1`), run Pass 1 three times with temperature=0.7 and take the majority-vote entity set. Entities appearing in ≥2/3 runs get `confidence += 0.15`.

**3. Cross-source triangulation**
When the same entity appears in multiple independent sources (different Slack threads, different commits, a ticket AND a doc), confidence is boosted:
```
confidence = base_confidence + 0.1 * min(source_count - 1, 3)
```
Maximum confidence (1.0) requires 4+ independent sources agreeing.

**4. Verification LLM is always a different model**
Pass 3 uses `deepseek-r1:14b` (Ollama) or `claude-opus-4-6` (Anthropic).  
Pass 4 verification uses `deepseek-coder-v2:16b` or `claude-sonnet-4-6`.  
Different models have different biases — cross-model verification catches more hallucinations than same-model self-critique.

**5. Source-anchored citations in every context entry**
Every `knowledge_context` row MUST have a `source_ref`. The graph builder rejects context without citations. Agents displaying context to users always render the citation.

---

## Layer 3: Signal Fusion — Multiple Sources, One Truth

The hardest problem: the same fact appears in five different places and they all say slightly different things. Which version is canonical?

### Fusion Algorithm

```python
async def fuse_context_entries(
    node_id: UUID,
    new_entry: ContextEntry,
    existing_entries: list[ContextEntry],
) -> FusionResult:
    
    # 1. Semantic deduplication — is this entry about the same claim?
    for existing in existing_entries:
        similarity = cosine_similarity(new_entry.embedding, existing.embedding)
        if similarity > 0.92:
            # Same claim, different source → strengthen confidence
            existing.confidence = min(1.0, existing.confidence + 0.1)
            existing.source_count += 1
            existing.source_refs.append(new_entry.source_ref)
            return FusionResult(action='strengthen', entry=existing)
    
    # 2. Contradiction detection — does this conflict with existing knowledge?
    contradictions = await llm.chat(
        role=TaskRole.REASONING,
        prompt=CONTRADICTION_PROMPT.format(
            existing=[e.content for e in existing_entries],
            new=new_entry.content,
        )
    )
    if contradictions.has_conflict:
        # Flag both entries, reduce confidence of older one
        flag_conflict(existing_entries, new_entry)
        return FusionResult(action='conflict_flagged', confidence_delta=-0.2)
    
    # 3. No match, no conflict → add as new context entry
    return FusionResult(action='add', entry=new_entry)
```

### Staleness Engine

Knowledge decays. The staleness engine runs nightly:

```python
async def compute_staleness_risk(node: KnowledgeNode) -> float:
    days_since_seen = (now() - node.last_seen).days
    
    # Base decay: 0.01 per day (fully stale after ~100 days without re-confirmation)
    base_decay = min(days_since_seen * 0.01, 0.5)
    
    # Amplifiers
    if node.source_count == 1:
        base_decay *= 1.5   # Single-source knowledge decays faster
    
    if node.entity_type in HIGH_CHURN_TYPES:  # ApiEndpoint, Policy
        base_decay *= 1.3
    
    # Dampeners
    if node.confidence >= 0.9:
        base_decay *= 0.7   # High-confidence nodes decay slower
    
    if has_human_annotation(node.id):
        base_decay *= 0.5   # Human-verified is more durable
    
    return min(base_decay, 1.0)
```

Nodes with `staleness_risk > 0.7` are flagged in the UI and agents are warned before using their context.

---

## Layer 4: The Skills File — What AI Agents Actually Consume

This is the key output that makes Company Brain different from a knowledge base. Instead of returning "here are documents about X," the Skills File API returns **structured, executable knowledge** that agents can act on directly.

### Skills File Schema

```json
{
  "skill_id": "uuid",
  "name": "handle_refund_request",
  "domain": "support",
  "version": "2024-01-20T10:00:00Z",
  "confidence": 0.91,
  "staleness_risk": 0.12,
  
  "trigger": {
    "description": "Customer requests a refund",
    "intent_signals": ["refund", "money back", "cancel order", "charge dispute"]
  },
  
  "context": {
    "policy_summary": "Refunds approved within 30 days of purchase, no questions asked. Digital goods excluded after download.",
    "authority_matrix": {
      "L1_support": {"max_amount": 100, "requires_manager": false},
      "L2_support": {"max_amount": 500, "requires_manager": false},
      "manager":    {"max_amount": null, "requires_manager": false}
    },
    "exceptions": [
      "Digital goods: non-refundable after download",
      "Subscriptions: pro-rated refund only",
      "After 90 days: requires manager approval regardless of amount"
    ]
  },
  
  "decision_tree": [
    {
      "check": "days_since_purchase",
      "condition": "> 90",
      "action": "escalate_to_manager",
      "reason": "Outside standard refund window"
    },
    {
      "check": "product_type",
      "condition": "== 'digital' AND download_confirmed",
      "action": "deny",
      "reason": "Digital goods policy"
    },
    {
      "check": "refund_amount",
      "condition": "> 100",
      "action": "escalate_to_l2"
    },
    {
      "check": "*",
      "action": "approve"
    }
  ],
  
  "related_skills": ["escalate_to_manager", "process_subscription_cancellation"],
  "blast_radius": ["billing_service", "stripe_integration", "customer_ledger"],
  
  "sources": [
    {"ref": "confluence://support-policies/refund-v3", "confidence": 0.95, "date": "2024-01-10"},
    {"ref": "slack://support-team/C1234567/2024-01-15", "confidence": 0.82, "date": "2024-01-15"},
    {"ref": "zendesk://macro/143", "confidence": 0.78, "date": "2023-12-01"}
  ]
}
```

### Skills File API

```
GET  /v1/skills?domain=support&trigger=refund&limit=5
     → Top-5 skills matching the trigger (semantic search)

GET  /v1/skills/{skill_id}
     → Full skills file for one skill

GET  /v1/skills/{skill_id}/blast-radius
     → What breaks (in any domain) if this skill changes?

POST /v1/skills/query
     Body: { "question": "how do we handle a refund for a digital product?", "domain": "support" }
     → Natural language → skill retrieval → LLM synthesis → grounded answer
```

---

## Layer 5: Query Engine — Two Modes, One Graph

### Mode A: Graph Traversal (current — fast, deterministic)
Used for blast radius, dependency chains, ownership queries.

```sql
-- "What is affected if I change this node?"
WITH RECURSIVE blast AS (
  SELECT target_id, edge_type, 1 AS depth
  FROM knowledge_edges
  WHERE source_id = :node_id
    AND workspace_id = :workspace_id
    AND NOT is_pruned
    AND confidence >= 0.5
  
  UNION ALL
  
  SELECT e.target_id, e.edge_type, b.depth + 1
  FROM knowledge_edges e
  JOIN blast b ON b.target_id = e.source_id
  WHERE b.depth < :max_hops
    AND e.workspace_id = :workspace_id
    AND NOT e.is_pruned
)
SELECT DISTINCT n.*, b.depth, b.edge_type
FROM blast b
JOIN knowledge_nodes n ON n.id = b.target_id
ORDER BY b.depth, n.confidence DESC;
```

### Mode B: Semantic + Graph Hybrid (new — for natural language)

```python
async def hybrid_query(question: str, workspace_id: str) -> QueryResult:
    
    # Step 1: Embed the question
    q_embedding = await embed(question)
    
    # Step 2: Find semantically similar nodes + context (pgvector)
    seed_nodes = await db.fetch("""
        SELECT id, name, domain, entity_type, confidence,
               1 - (embedding <=> $1) AS similarity
        FROM knowledge_nodes
        WHERE workspace_id = $2
          AND NOT is_pruned
          AND staleness_risk < 0.7
        ORDER BY embedding <=> $1
        LIMIT 10
    """, q_embedding, workspace_id)
    
    # Step 3: Expand each seed node via graph traversal (1-2 hops)
    #         This brings in related nodes the embedding didn't surface
    graph_context = await expand_neighbourhood(seed_nodes, max_hops=2)
    
    # Step 4: Fetch context entries for all relevant nodes
    context_entries = await fetch_context(
        node_ids=[n.id for n in graph_context],
        context_types=['llm_synthesis', 'human_annotation'],
        min_confidence=0.6,
    )
    
    # Step 5: LLM synthesis with citations
    answer = await llm.chat(
        role=TaskRole.QUERY,
        system=GROUNDED_SYNTHESIS_PROMPT,
        user=QUERY_PROMPT.format(
            question=question,
            context=format_context(context_entries),
        )
    )
    
    return QueryResult(
        answer=answer.text,
        sources=[e.source_ref for e in context_entries],
        affected_nodes=graph_context,
        confidence=mean([n.confidence for n in seed_nodes]),
    )
```

**Why hybrid matters:** Pure semantic search finds "similar documents." Pure graph traversal finds "connected nodes." The hybrid finds *semantically relevant starting points and then traverses the actual dependency structure* — giving answers that are both topically correct AND structurally complete.

---

## Scalability Design

### Ingestion Scaling

| Concern | Solution |
|---------|----------|
| Slow LLM calls block ingestion | Async pipeline with asyncio.Semaphore(10) per LLM role |
| Large repos overwhelm context | Chunker with 200k char budget + age-based truncation (v1) |
| Multiple sources for same entity | Signal fusion with semantic dedup before DB write |
| Re-ingesting unchanged content | SHA-256 hash of chunk content; skip if hash exists in `ingestion_log` |

### Graph Query Scaling

| Concern | Solution |
|---------|----------|
| Deep traversals get slow | Max hops configurable (default 3); add breadth limit |
| Embedding search slow at scale | ivfflat index; partition by domain at 1M+ nodes |
| Blast radius cache invalidation | Cache key = `node_id:workspace_id:hop_depth`; invalidate on edge upsert |
| Cross-domain queries | Domain filter is an index prefix; very fast even across large graphs |

### LLM Cost Scaling

| Concern | Solution |
|---------|----------|
| Re-extracting unchanged chunks | Content hash check; only extract if content changed |
| Verification pass expensive | Run Pass 4 only for nodes with confidence < 0.8 or source_count == 1 |
| Embedding cost | Batch embed 100 texts per API call; cache embeddings for 30 days |
| Ollama saturation | Queue-based ingestion; OLLAMA_NUM_PARALLEL=2 per GPU |

---

## Cross-Domain Blast Radius — The Killer Feature

The most powerful query this architecture enables that nothing else can answer:

**"What business processes break if I change the `charge.amount` column?"**

```
charge.amount (DatabaseColumn, engineering)
  → ChargeService reads_from
    → /api/v1/charge ApiEndpoint
      → RefundCalculator code_function
        → RefundPolicy (Policy, support)    ← crosses into support domain
          → L1_handle_refund (Skill, support)
          → invoice_generation (Process, finance)  ← crosses into finance
```

This answer requires traversing across engineering → support → finance in a single graph query. No other tool in the market does this. It's only possible because all domains share the same graph primitive.

---

## Agent Consumption Patterns

Three ways AI agents consume Company Brain:

### Pattern 1: Skill Lookup (for task agents)
```python
# Agent handling a support ticket
skills = await company_brain.get_skills(
    trigger="customer requests refund",
    domain="support",
    limit=3
)
# Agent gets a structured decision tree, not a blob of text
# It can execute the decision tree directly without further LLM reasoning
```

### Pattern 2: Blast Radius Check (for code agents / CI)
```python
# Before merging a PR that changes a DB column
affected = await company_brain.blast_radius(
    node_external_id="public.charges.amount_cents",
    max_hops=4
)
if any(n.domain == 'support' for n in affected.nodes):
    pr.add_comment("⚠️ This change affects support playbooks — review RefundPolicy")
```

### Pattern 3: Grounded Q&A (for general-purpose agents)
```python
# Agent answering "how do we handle X?"
answer = await company_brain.query(
    question="how do we handle a refund request from a enterprise customer after 90 days?",
    domain="support",
)
# Returns: structured answer + source citations + confidence score
# Agent can choose to show sources to the human for verification
```

---

## Implementation Roadmap

### Phase 1 (Current — Engineering Domain)
- [x] PostgreSQL graph store with RLS
- [x] Git collector and 4-pass extraction pipeline
- [x] Blast radius via recursive CTE
- [x] React dashboard + query UI
- [ ] pgvector integration + semantic search (ADR-004 migration)
- [ ] Hybrid query mode (semantic + graph)

### Phase 2 (Universal Schema + Multi-Source)
- [ ] Flyway V2 migration to `knowledge_nodes` schema
- [ ] Domain registry API
- [ ] Slack collector (highest signal-to-noise after git)
- [ ] Signal fusion engine
- [ ] Staleness engine (nightly job)
- [ ] Skills File API

### Phase 3 (Cross-Domain + Agent SDK)
- [ ] Zendesk / Confluence collectors
- [ ] Cross-domain blast radius
- [ ] Skills file generation pipeline
- [ ] Company Brain agent SDK (Python + TypeScript clients)
- [ ] Webhook-based cache invalidation for real-time staleness

### Phase 4 (Scale + Enterprise)
- [ ] Per-workspace embedding index partitioning
- [ ] Self-consistency sampling for low-confidence nodes
- [ ] Human-in-the-loop verification workflow (high-stakes nodes)
- [ ] Audit log for all knowledge mutations
- [ ] SOC 2 compliance (encryption at rest for `knowledge_context.content`)
