# Company Brain — Retrieval Architecture Deep Dive

> The core thesis: **don't put the company brain into the LLM's context window. Put the LLM at the edge of the graph.**

---

## The Problem, Stated Precisely

A mid-sized company (200 engineers, 50 support agents, $10M ARR) generates approximately:

| Source | Facts | Tokens |
|--------|-------|--------|
| Git history (2 years) | ~50k commits × 10 facts = 500k nodes | ~200M |
| Slack (1 year) | ~1M messages → ~100k distilled facts | ~50M |
| Support tickets (1 year) | ~50k tickets → ~30k distilled facts | ~15M |
| Confluence/Notion | ~2k docs → ~20k facts | ~10M |
| **Total** | **~650k nodes, ~5M context entries** | **~275M tokens** |

Even Claude's 200k token window holds 0.07% of this. Stuffing context in doesn't scale.

But the deeper problem isn't the token limit. It's **attention dilution**. Research consistently shows LLMs lose track of relevant information when it appears beyond position ~20k in a long context, even when the window supports it. The model confidently answers from the wrong section.

The solution isn't a bigger context window. It's **surgical retrieval** — give the LLM only the 10–20 facts it actually needs, sourced from a graph that knows which facts are structurally related to the question.

---

## Architecture: Three Retrieval Strategies

The system supports three retrieval strategies, each appropriate for a different class of query. The router selects automatically based on query analysis.

```
┌─────────────────────────────────────────────────────┐
│                   Query Router                       │
│  (classifies intent → selects retrieval strategy)   │
└──────────────┬──────────────┬───────────────────────┘
               │              │              │
               ▼              ▼              ▼
        Strategy 1     Strategy 2      Strategy 3
      Graph Traversal  Hybrid Recall  Agentic Walk
      (structured)    (semantic+graph) (LLM-driven)
```

### Strategy 1 — Graph Traversal (Structured Queries)
For: blast radius, dependency chains, ownership, "what calls what"  
Latency: ~50ms  
Context budget: small (15–30 nodes)

```python
# "What breaks if I change charge.amount_cents?"
result = await blast_radius(node_id, max_hops=3, min_confidence=0.6)
# Returns: structured list of affected nodes, no LLM needed for traversal
# LLM used only for final synthesis (5-10 node summaries)
```

### Strategy 2 — Hybrid Recall (Natural Language Questions)
For: "how does X work?", "who owns Y?", "what's the policy on Z?"  
Latency: ~300ms  
Context budget: medium (20–40 context entries)

```
embedding(question) → top-10 nodes (pgvector)
         ↓
  graph expand 1-2 hops → candidate set ~50 nodes
         ↓
  reranker → top-15 most relevant nodes
         ↓
  fetch context entries → ~30 context entries
         ↓
  LLM synthesis (context window: ~8k tokens)
```

### Strategy 3 — Agentic Graph Walk (Complex / Multi-Step)
For: RCAs, onboarding, cross-domain impact, design reviews  
Latency: ~2–8s (multi-turn LLM)  
Context budget: dynamically managed (the LLM decides what to fetch)

```
LLM receives: question + graph_tools
LLM iteratively calls:
  → lookup_node(name_or_id)
  → traverse(node_id, direction, edge_types, hops)
  → get_context(node_id)
  → search(semantic_query)
Until: "I have enough context to answer"
Then: synthesise with citations
```

This is the strategy that handles your full use case list (code review, onboarding, RCA, revenue visibility, etc.) because it lets the LLM decide which parts of the graph are relevant rather than pre-programming the retrieval logic.

---

## Multi-Level Summarization — The Knowledge Pyramid

The graph stores facts at four levels of abstraction. Retrieval always starts at the top and drills down.

```
L3: Company Overview          1 document, ~1,000 tokens
    "How this company works, its domains, key systems"

L2: Domain Summaries          1 per domain, ~500 tokens each
    "What the engineering domain contains: X services,
     Y APIs, key dependencies, current risk areas"

L1: Node Summaries            1 per node, ~100 tokens
    "charge_service: processes payments, owns the
     charges table, called by checkout and subscription"

L0: Context Entries           N per node, ~200 tokens each
    "Source: git commit a3f2c1 — refactored amount_cents
     from INT to BIGINT after overflow in prod (2024-01-15)"
```

Agents start at L3. They drill to L2 for domain orientation, L1 for node identification, and L0 only for the specific nodes they need. A typical query touches:

- L3: always (1 doc, ~1k tokens)
- L2: 1–2 domains (~1k tokens)
- L1: 10–20 nodes (~2k tokens)
- L0: 5–10 specific context entries (~2k tokens)

**Total context per query: ~6k–8k tokens, regardless of company size.**

### Auto-Generating the Pyramid

L1 and L2 summaries are generated and cached. They refresh when the underlying nodes change.

```python
async def refresh_node_summary(node: KnowledgeNode) -> str:
    """L1 summary: one sentence about what this node is and does."""
    context_entries = await fetch_context(node.id, limit=5, min_confidence=0.7)
    
    summary = await llm.chat(
        role=TaskRole.FAST,  # cheap model, this runs millions of times
        system="""Generate a single sentence (max 25 words) describing what this
                  entity is and its primary role. Be factual. Use only what's
                  in the provided context. Do not infer or guess.""",
        user=f"Entity: {node.entity_type} '{node.name}'\n"
             f"Domain: {node.domain}\n"
             f"Context:\n" + "\n".join(e.content for e in context_entries),
    )
    
    await db.execute(
        "UPDATE knowledge_nodes SET summary = $1 WHERE id = $2",
        summary.text, node.id
    )
    return summary.text


async def refresh_domain_summary(workspace_id: UUID, domain: str) -> str:
    """L2 summary: paragraph about a whole domain."""
    # Pull all L1 summaries for this domain (can be thousands, batch them)
    node_summaries = await db.fetch("""
        SELECT entity_type, name, summary, confidence
        FROM knowledge_nodes
        WHERE workspace_id = $1 AND domain = $2
          AND summary IS NOT NULL AND NOT is_pruned
        ORDER BY confidence DESC
        LIMIT 200
    """, workspace_id, domain)
    
    summary = await llm.chat(
        role=TaskRole.BALANCED,
        system="""Write a 2-paragraph overview of this domain for a new employee.
                  Paragraph 1: what this domain covers and its key components.
                  Paragraph 2: the most important dependencies and risk areas.
                  Base this ONLY on the provided node summaries.""",
        user=f"Domain: {domain}\n\nNodes:\n" +
             "\n".join(f"- {n['entity_type']} '{n['name']}': {n['summary']}"
                       for n in node_summaries),
    )
    return summary.text
```

---

## Speculative Indexing — Index Questions, Not Just Content

Standard RAG embeds the content and matches it against the embedded question. The semantic gap between "how questions sound" and "how answers sound" causes retrieval misses.

**Speculative indexing** pre-computes the questions that each piece of context would answer, then indexes those questions. At retrieval time, you match question→question instead of question→answer — much higher precision.

```python
async def speculatively_index_context_entry(entry: ContextEntry) -> None:
    """
    Given a context entry, generate the questions it answers.
    Store question embeddings alongside the content embedding.
    """
    questions_response = await llm.chat(
        role=TaskRole.FAST,
        system="""Generate 3-5 natural language questions that this context entry
                  would answer well. Output as JSON array of strings only.
                  Questions should be phrased as a developer or analyst would ask them.""",
        user=f"Context:\n{entry.content}\n\nSource: {entry.source_ref}",
    )
    
    questions = json.loads(questions_response.text)
    
    for question in questions:
        embedding = await embed(question)
        await db.execute("""
            INSERT INTO context_question_index
                (context_entry_id, question, embedding)
            VALUES ($1, $2, $3)
        """, entry.id, question, embedding)


# At retrieval time — match against pre-computed questions
async def retrieve_by_speculative_index(
    query: str,
    workspace_id: UUID,
    limit: int = 20,
) -> list[ContextEntry]:
    q_embedding = await embed(query)
    
    # Match against pre-indexed questions (much more precise than content match)
    rows = await db.fetch("""
        SELECT ce.*, 1 - (cqi.embedding <=> $1) AS similarity
        FROM context_question_index cqi
        JOIN knowledge_context ce ON ce.id = cqi.context_entry_id
        JOIN knowledge_nodes n ON n.id = ce.node_id
        WHERE n.workspace_id = $2
          AND ce.confidence >= 0.6
          AND n.staleness_risk < 0.7
        ORDER BY cqi.embedding <=> $1
        LIMIT $3
    """, q_embedding, workspace_id, limit)
    
    # Deduplicate by context entry (multiple questions may point to same entry)
    seen = set()
    results = []
    for row in rows:
        if row['id'] not in seen:
            seen.add(row['id'])
            results.append(row)
    return results
```

**Why this matters:** Consider the context entry: *"The amount_cents field overflowed to negative in production on 2024-01-15 when a single transaction exceeded $21M."* A standard embedding search for "what are the limits of the amount field?" would likely miss this. A speculative index would have pre-generated "what is the maximum value amount_cents can hold?" and matched it precisely.

---

## Agentic Graph Walk — Technical Design

The agentic strategy gives the LLM a set of graph tools and lets it navigate to the answer. This is the right approach for complex queries that no fixed retrieval algorithm can anticipate.

### Tool Definitions for the LLM

```python
GRAPH_TOOLS = [
    {
        "name": "lookup_node",
        "description": "Find a node by name or keyword. Returns node metadata and a 1-sentence summary.",
        "parameters": {
            "query": "string — name or description to search for",
            "domain": "string (optional) — filter by domain",
            "entity_type": "string (optional) — filter by type",
            "limit": "int (default 5)"
        }
    },
    {
        "name": "traverse",
        "description": "Follow edges from a node. Returns neighbouring nodes and edge types.",
        "parameters": {
            "node_id": "UUID",
            "direction": "'outbound' | 'inbound' | 'both'",
            "edge_types": "list[string] (optional) — filter by edge type",
            "max_hops": "int (default 2, max 4)",
            "min_confidence": "float (default 0.6)"
        }
    },
    {
        "name": "get_context",
        "description": "Fetch detailed context entries for a node. Use after traverse to get full detail.",
        "parameters": {
            "node_id": "UUID",
            "context_types": "list[string] (optional)",
            "limit": "int (default 5)"
        }
    },
    {
        "name": "semantic_search",
        "description": "Semantic search across context entries. Best for 'how does X work?' queries.",
        "parameters": {
            "query": "string",
            "domain": "string (optional)",
            "limit": "int (default 10)"
        }
    },
    {
        "name": "get_domain_summary",
        "description": "Get a high-level overview of a domain. Good starting point for onboarding queries.",
        "parameters": {
            "domain": "string"
        }
    },
    {
        "name": "synthesise",
        "description": "CALL THIS LAST. Synthesise all retrieved context into a final answer with citations.",
        "parameters": {
            "question": "string",
            "context_node_ids": "list[UUID] — nodes you found relevant"
        }
    }
]
```

### Agentic Walk Execution

```python
async def agentic_walk(question: str, workspace_id: UUID) -> QueryResult:
    messages = [
        {
            "role": "system",
            "content": AGENTIC_SYSTEM_PROMPT.format(
                company_overview=await get_company_overview(workspace_id),
                available_domains=await list_domains(workspace_id),
            )
        },
        {"role": "user", "content": question}
    ]
    
    tool_calls_made = []
    max_iterations = 8  # prevent infinite loops
    
    for iteration in range(max_iterations):
        response = await llm.chat_with_tools(
            role=TaskRole.QUERY,
            messages=messages,
            tools=GRAPH_TOOLS,
        )
        
        if response.stop_reason == "end_turn":
            # LLM decided it has enough; extract final answer
            break
        
        if response.tool_use:
            tool_name = response.tool_use.name
            tool_input = response.tool_use.input
            tool_calls_made.append((tool_name, tool_input))
            
            # Execute the tool call against the real graph
            tool_result = await execute_graph_tool(
                tool_name, tool_input, workspace_id
            )
            
            # Feed result back into the conversation
            messages.append({"role": "assistant", "content": response.content})
            messages.append({
                "role": "user",
                "content": [{"type": "tool_result",
                             "tool_use_id": response.tool_use.id,
                             "content": json.dumps(tool_result)}]
            })
    
    return QueryResult(
        answer=response.text,
        tool_calls=tool_calls_made,
        context_nodes_visited=[tc[1].get('node_id') for tc in tool_calls_made
                               if 'node_id' in tc[1]],
    )
```

### Example Agentic Walk — Onboarding a New Engineer

**Question:** *"I'm joining the payments team. What do I need to understand first?"*

```
Iteration 1: LLM calls get_domain_summary("engineering")
  → Gets paragraph overview of all engineering services

Iteration 2: LLM calls lookup_node("payments", domain="engineering")
  → Returns: PaymentService, ChargeService, RefundService, StripeIntegration

Iteration 3: LLM calls traverse(ChargeService.id, direction="both", max_hops=2)
  → Returns: upstream callers (Checkout, Subscription), downstream (charges table, Stripe)

Iteration 4: LLM calls get_context(ChargeService.id, context_types=["human_annotation", "llm_synthesis"])
  → Returns: "amount_cents overflow incident", "idempotency key pattern", "Stripe API v3 migration"

Iteration 5: LLM calls get_context(charges_table.id)
  → Returns: column definitions, known invariants, staleness alert on "amount_cents limits"

Iteration 6: LLM calls synthesise(question, [ChargeService, charges_table, RefundService, ...])
  → Final answer with structured onboarding guide, citations, recommended reading order
```

Total context window used at synthesis: ~6k tokens. No information overload. No hallucination about facts the system doesn't have.

---

## Use-Case-Specific Context Shaping

Each use case gets a different **retrieval profile** — same underlying graph, different traversal parameters and synthesis prompts.

```python
RETRIEVAL_PROFILES = {

    "blast_radius": RetrievalProfile(
        strategy=Strategy.GRAPH_TRAVERSAL,
        direction="outbound",
        max_hops=4,
        min_confidence=0.5,
        synthesis_prompt=BLAST_RADIUS_PROMPT,  # "what could break and why"
        include_domains=None,  # all domains
    ),

    "code_review": RetrievalProfile(
        strategy=Strategy.HYBRID,
        direction="both",
        max_hops=2,
        min_confidence=0.7,
        synthesis_prompt=CODE_REVIEW_PROMPT,  # "risks, invariants, prior decisions"
        include_context_types=["human_annotation", "risk_flag", "invariant"],
    ),

    "onboarding": RetrievalProfile(
        strategy=Strategy.AGENTIC,
        synthesis_prompt=ONBOARDING_PROMPT,   # "structured reading order for a newcomer"
        abstraction_level="L2",               # domain summaries first, not raw context
        include_context_types=["llm_synthesis", "human_annotation"],
    ),

    "rca": RetrievalProfile(
        strategy=Strategy.AGENTIC,
        temporal_window_days=30,              # only context from last 30 days
        direction="inbound",                  # traverse backwards from incident
        synthesis_prompt=RCA_PROMPT,          # "timeline, root causes, contributing factors"
        include_edge_types=["DEPENDS_ON", "CALLS", "WRITES_TO"],
    ),

    "design_review": RetrievalProfile(
        strategy=Strategy.HYBRID,
        max_hops=3,
        synthesis_prompt=DESIGN_REVIEW_PROMPT, # "constraints, prior decisions, ADRs"
        include_context_types=["human_annotation", "llm_synthesis"],
        include_domains=["engineering", "ops"],
    ),

    "revenue_visibility": RetrievalProfile(
        strategy=Strategy.HYBRID,
        synthesis_prompt=REVENUE_PROMPT,        # "executive-level, no technical jargon"
        abstraction_level="L2",
        include_domains=["finance", "support"],
        audience="leadership",                  # shapes synthesis verbosity and vocabulary
    ),
}
```

The synthesis prompt is where most of the use-case intelligence lives. The graph traversal is generic — the prompt tells the LLM how to interpret and present what it finds.

---

## Context Budget Management

Every query has a token budget. The retrieval system enforces it.

```python
CONTEXT_BUDGETS = {
    "blast_radius":       ContextBudget(nodes=30, context_entries=10, max_tokens=6_000),
    "code_review":        ContextBudget(nodes=15, context_entries=20, max_tokens=8_000),
    "onboarding":         ContextBudget(nodes=40, context_entries=15, max_tokens=10_000),
    "rca":                ContextBudget(nodes=20, context_entries=30, max_tokens=12_000),
    "design_review":      ContextBudget(nodes=20, context_entries=20, max_tokens=8_000),
    "revenue_visibility": ContextBudget(nodes=10, context_entries=10, max_tokens=4_000),
    "query":              ContextBudget(nodes=15, context_entries=15, max_tokens=7_000),
}

async def enforce_budget(
    nodes: list[KnowledgeNode],
    context_entries: list[ContextEntry],
    budget: ContextBudget,
) -> tuple[list, list]:
    """
    Trim to budget using priority scoring.
    Priority = confidence × recency_factor × (1 - staleness_risk)
    """
    def priority(node):
        recency = 1.0 / (1 + (now() - node.last_seen).days / 30)
        return node.confidence * recency * (1 - node.staleness_risk)
    
    # Sort and trim nodes
    nodes = sorted(nodes, key=priority, reverse=True)[:budget.nodes]
    
    # Sort context entries by confidence × recency, trim to budget
    context_entries = sorted(
        context_entries,
        key=lambda e: e.confidence * (1 / (1 + (now() - e.extracted_at).days / 30)),
        reverse=True
    )[:budget.context_entries]
    
    # Final token count check — truncate content if still over budget
    total_tokens = sum(estimate_tokens(e.content) for e in context_entries)
    if total_tokens > budget.max_tokens:
        context_entries = truncate_to_token_budget(context_entries, budget.max_tokens)
    
    return nodes, context_entries
```

---

## Synthesis Prompt Engineering — Getting Quality LLM Output

The retrieval architecture guarantees the RIGHT facts reach the LLM. The synthesis prompt ensures the LLM uses them correctly.

### The Non-Negotiable Rules in Every Synthesis Prompt

```python
BASE_SYNTHESIS_RULES = """
RULES YOU MUST FOLLOW:
1. Base your answer ONLY on the provided context. Do not use training knowledge.
2. For every factual claim, add a citation: [source: <source_ref>]
3. If the context contradicts itself, say so explicitly. Do not pick one silently.
4. If you cannot answer from the provided context, say: "The Company Brain does not
   have sufficient information about X. Consider annotating [node name]."
5. If any context has staleness_risk > 0.6, prefix it: "⚠️ This may be outdated."
6. Output confidence as a number 0.0–1.0 at the end of your answer.
"""
```

### Example: Code Review Synthesis Prompt

```python
CODE_REVIEW_PROMPT = BASE_SYNTHESIS_RULES + """
You are reviewing a code change that affects the nodes listed below.
Your job: surface the risks, invariants, and institutional knowledge a reviewer needs.

Structure your review as:
## Blast Radius
<what else is affected by this change>

## Known Invariants
<rules that must not be broken, from human annotations>

## Historical Context
<relevant past decisions, incidents, migrations>

## Risk Flags  
<anything marked as risk_flag in the context>

## Suggested Review Checklist
<3-5 concrete things the reviewer should verify>
"""
```

### Example: RCA Synthesis Prompt

```python
RCA_PROMPT = BASE_SYNTHESIS_RULES + """
You are building a Root Cause Analysis for an incident.
Use only the provided context entries, which are ordered by timestamp.

Structure your output as:
## Timeline
<chronological sequence of changes and events that contributed>

## Root Cause
<the primary cause, grounded in specific context entries with citations>

## Contributing Factors
<other nodes or changes that amplified the impact>

## What the Graph Shows Was At Risk
<nodes with staleness_risk > 0.5 or low confidence that were in the blast radius>

## Recommended Follow-Up
<knowledge gaps to fill, annotations to add, edges to verify>
"""
```

---

## The Complete Retrieval Flow

```
                         USER QUERY
                              │
                    ┌─────────▼─────────┐
                    │   Query Router     │
                    │ (classify intent) │
                    └─────────┬─────────┘
          ┌──────────────────┼──────────────────┐
          │                  │                  │
          ▼                  ▼                  ▼
   GRAPH TRAVERSAL      HYBRID RECALL      AGENTIC WALK
   (structured)         (NL questions)     (complex/multi-step)
          │                  │                  │
          │           ┌──────▼──────┐          │
          │           │  pgvector   │          │
          │           │  top-10     │          │
          │           │  seed nodes │          │
          │           └──────┬──────┘          │
          │                  │                  │
          └─────────────────►▼◄────────────────┘
                    ┌─────────────────┐
                    │  Graph Expand   │
                    │  (1-4 hops CTE) │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │   Reranker      │  ← priority(confidence × recency × !staleness)
                    │  (trim to      │
                    │   budget)       │
                    └────────┬────────┘
                             │
               ┌─────────────▼──────────────┐
               │    Context Assembly         │
               │  L3 overview (always)       │
               │  L2 domain summaries        │
               │  L1 node summaries          │
               │  L0 context entries         │
               │  + staleness warnings       │
               │  + source citations         │
               └─────────────┬──────────────┘
                             │
                    ┌────────▼────────┐
                    │  LLM Synthesis  │
                    │  (use-case      │
                    │   prompt)       │
                    └────────┬────────┘
                             │
                    ┌────────▼────────┐
                    │    Response     │
                    │  answer         │
                    │  citations      │
                    │  confidence     │
                    │  affected_nodes │
                    └─────────────────┘
```

---

## Benchmarking the Retrieval Quality

Before trusting the system in production, retrieval quality must be measured.

```python
# Evaluation dataset: 50 questions with known ground-truth answers
# Each question has: the correct answer, the correct source nodes, confidence

async def evaluate_retrieval(eval_dataset: list[EvalItem]) -> RetrievalMetrics:
    results = []
    for item in eval_dataset:
        retrieved = await retrieve(item.question, item.workspace_id)
        
        # Recall: did we retrieve the correct nodes?
        ground_truth_ids = set(item.correct_node_ids)
        retrieved_ids = set(n.id for n in retrieved.nodes)
        recall = len(ground_truth_ids & retrieved_ids) / len(ground_truth_ids)
        
        # Precision: are retrieved nodes actually relevant?
        precision = len(ground_truth_ids & retrieved_ids) / len(retrieved_ids)
        
        # Faithfulness: does the answer only use retrieved context?
        faithfulness = await score_faithfulness(item.question, retrieved, item.answer)
        
        results.append({"recall": recall, "precision": precision,
                        "faithfulness": faithfulness})
    
    return RetrievalMetrics(
        mean_recall=mean(r["recall"] for r in results),
        mean_precision=mean(r["precision"] for r in results),
        mean_faithfulness=mean(r["faithfulness"] for r in results),
    )

# Target metrics for production readiness:
# recall >= 0.85   (find the right nodes 85% of the time)
# precision >= 0.70 (70% of retrieved nodes are actually relevant)
# faithfulness >= 0.90 (answers grounded in retrieved context 90% of the time)
```

---

## Summary: Why This Scales

| Challenge | Solution |
|-----------|----------|
| Company has 650k nodes, won't fit in context | Multi-level pyramid; query touches ~50 nodes max |
| LLMs lose track in long context | Retrieval budget enforced; context stays ~6-8k tokens |
| Different use cases need different context | Retrieval profiles: different traversal + synthesis per use case |
| Semantic search misses precise technical terms | Speculative indexing: pre-index questions, not content |
| Fixed retrieval algorithm can't handle complex queries | Agentic walk: LLM decides which graph edges to follow |
| Knowledge changes; retrieved facts may be stale | Staleness warnings embedded in context assembly |
| Can't tell if the system is working | Automated eval suite with recall/precision/faithfulness benchmarks |
