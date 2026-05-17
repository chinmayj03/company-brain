# Ship Log — company-brain V2

Each row = one shipped sub-session. Evidence = PR link + commit hash.

| Date | Sub-session | ADR(s) | PR # | Commit |
|------|-------------|--------|------|--------|
| — | ADR-0011 structural-first extraction | 0011 | #1 | dc38be8 |
| — | ADR-0012 BrainStore JSON SoT | 0012 | #2, #5 | a5b8b8e |
| — | ADR-0013 Canonical URN identity | 0013 | #3 | d7b6d9d |
| — | ADR-0014 Persistent L2 context | 0014 | #4 | b73a10c |
| — | ADR-0015 Qdrant hybrid retriever (BM25S+voyage+RRF) | 0015 | #15 | e003a4f |
| — | ADR-0016 Repo-scoped CLI | 0016 | #7 | aa4be38 |
| — | ADR-0017 assumption+business_context first-class | 0017 | #8 | 11a151e |
| — | ADR-0018 Smart-zone context assembler | 0018 | — | 7baaea4 |
| — | ADR-0019 MCP stdio server | 0019 | — | f1bc0d9 |
| — | ADR-0042 extraction pipeline P2/P3/P4 | 0042 | #59 | ee1ed4b |
| — | ADR-0043 Qdrant multi-granularity + confidence dedup | 0043 | — | 5c3d61f |
| — | ADR-0049 aggressive caching + pipeline cost cuts | 0049 | #46 | a90277a |
| — | ADR-0051 P1-P4 harness loop + hooks + streaming | 0051 | #51 | 8838dfa |
| — | ADR-0055 cross-file cross-cutting extraction | 0055 | #60 | 288ed5b |
| — | ADR-0056 verifier loop self-correction | 0056 | #62 | dd320cd |
| — | ADR-0057 universal file extraction | 0057 | #61 | fd5af92 |
| — | ADR-0058 generated-code & schema-format awareness | 0058 | #65 | 90aff30 |
| — | ADR-0059 temporal and domain inference passes | 0059 | #64 | d82b22f |
| — | ADR-0060 BusinessContext v2 + 30-example few-shot library | 0060 | #77 | a34508368 |
| — | T1.2: ADR-0061 P1 iterative exploration | 0061 | #66 | bbfc423 |
| — | T1.1: P0 Bug Bundle (B1-B6) | — | #68 | c8f1243 |
| — | T1.4: ADR-0072 Frontend Product Completion APIs | 0072 | #70 | 3a5a43d |
| — | T1.3: ADR-0073 Frontend Demo Live-Up | 0073 | — | ff3604d |
| — | ADR-0071 Brain Browser route | 0071 | — | dffbff9 |
| — | T1.5+T1.6: ADR-0074 Source Registry + ADR-0075 UX Navigation | 0074, 0075 | #78 | d0e08de |
| 2026-05-17 | T1.7: ADR-0076 Frontend Rendering (React Flow, TanStack Query) | 0076 | #80 | 6d40dee |
| 2026-05-17 | A1.1: SQL Deep Extractor (sqlglot + embedded Java scanner) | — | #82 | 3a8e6c1 |
| 2026-05-17 | T1.10: ADR-0082 P1 Drift as first-class entity | 0082 | #83 | 579a07c |
| 2026-05-17 | B1.1: ADR-0091 Domain-Entity-First framing (writing) | 0091 | — | this PR |

## In progress (agents running as of 2026-05-17)

| # | Sub-session | Branch | Status |
|---|-------------|--------|--------|
| T1.8 | ADR-0064 P1 Privacy + Audit | feature/adr-0064-p1-privacy-audit | Agent running |
| T1.9 | ADR-0079 P1 Persona Templates | feature/adr-0079-p1-persona-templates | Agent running |
| T1.11 | ADR-0090 P1 Event-Stream M1+M2 | feature/adr-0090-p1-event-stream | Agent running |

## Queued next

| # | Sub-session | Unblocks |
|---|-------------|---------|
| A1.4 | Verbalized Confidence | T1.1 ✅ |
| A1.2 | Hybrid Retrieval V2 (BGE reranker) | T1.1 ✅ |
| A1.6 | Glossary Auto-Discovery | T1.8 |
| B1.2 | ADR-0092 Connector Framework | B1.1 ✅ |
| B1.3 | ADR-0093 Cross-Source Entity Resolution | B1.2 |
| B1.4 | Notion Connector | B1.2 |
