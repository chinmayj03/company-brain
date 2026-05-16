# ADR-0070 — PRD / Document Ingestion (PDFs, Notion, Confluence, Figma comments, Slack threads)

**Status:** Proposed
**Date:** 2026-05-11
**Builds on:** ADR-0057 (universal file extraction — handles in-repo `.md` docs already)
**Sequenced with:** P3 per DEMO-PRIORITY-ORDER.md — defer if no time before demo, but the "your brain reads your PRDs too" moment is a strong investor wow.

---

## Context

ADR-0057 made the brain extract any in-repo file: docs, configs, infra, CI. That's a big step. **But the most valuable institutional knowledge isn't in the repo at all.** It lives in:

- **Notion / Confluence** — PRDs, design docs, runbooks, architecture decisions written by PM+Eng but never committed to git
- **Figma comments** — designer↔engineer rationale on the UI behind the code
- **Slack** — incident post-mortems, "why did we decide X" threads
- **Linear / Jira tickets** — the user story behind every change
- **Loom videos** — engineering demos and decision recordings (transcript-extractable)
- **Standalone PDF specs** — vendor specs, regulatory docs, third-party API contracts that the team uploads ad-hoc
- **Email threads** — the "thinking" behind architectural decisions before they were written down

When an engineer asks the brain *"why does the lob column exist?"*, the answer might literally be in a Notion PRD from 2 years ago: *"Lob = Line of Business; required by the Acme contract Q3-2024 to segment commercial-vs-medicare reporting."*. The code doesn't say this. The git history hints at it. The PRD spells it out.

**Without this ADR**, the brain is a code-only knowledge graph. **With it**, the brain becomes the institutional memory of the entire engineering org — code + design + product + ops + decisions.

---

## Decision

Three coordinated mechanisms:

### M1 — Document Connector framework

A pluggable connector pattern. Each connector pulls documents from a source, dispatches to a content-type-aware extractor, and writes Document entities to the brain.

```
DocumentConnector (interface)
  ├── NotionConnector              — reads pages via Notion API
  ├── ConfluenceConnector           — reads pages via Confluence REST API
  ├── FigmaConnector                — reads file comments via Figma API
  ├── SlackConnector                — reads channel/thread messages via Slack API
  ├── LinearConnector               — reads tickets via Linear GraphQL
  ├── JiraConnector                 — reads tickets via Jira REST
  ├── PDFConnector                  — local file or URL; uses pdfminer.six
  ├── LoomConnector                 — fetches transcript via Loom API
  └── EmailConnector                — Gmail/MS365 OAuth; reads labeled threads
```

Connectors share a contract (`pull_documents(since: datetime, scope: ConnectorScope) -> list[Document]`). Each is ~150-300 LOC + auth + tests.

### M2 — Document entity + content extraction

```python
@dataclass
class Document:
    entity_type: str = "Document"
    name: str                          # "PRD: Lob Reporting Requirements"
    source: Literal["notion", "confluence", "figma", "slack", "linear", "jira", "pdf", "loom", "email"]
    source_id: str                     # connector-specific ID
    source_url: str                    # link back to the original
    title: str
    content_md: str                    # extracted as markdown (single canonical format)
    authors: list[str]
    created_at: datetime
    last_modified_at: datetime
    section_count: int                 # for very long docs we chunk further
    semantic_tag: Optional[str]        # auto-classified: "PRD" | "ADR" | "Runbook" | "Decision" | "Discussion" | "Spec"
```

Each document is also CHUNKED into `DocumentSection` entities (one per H2/H3 heading or every ~500 tokens for unstructured sources like Slack threads). Sections are individually embedded — so a query about "the lob column origin" can hit the right SECTION of a 30-page PRD, not the whole doc.

### M3 — Cross-reference inference (the killer feature)

After ingesting documents AND extracting code (existing pipeline), run a cross-reference inference pass:

```
For each Document or DocumentSection:
  Extract code-like tokens from its content (table names, class names,
  endpoint paths, ADR numbers, ticket IDs).
  For each token, look up matching code entities in the brain.
  When matched, emit DOCUMENTS edge: Document → CodeEntity.
```

**Result**: querying `getPayerCompetitors` returns not just code citations but ALSO Notion/Confluence pages that reference it. The lob query now answers "the column exists because PRD 'Q3-2024 Lob Reporting' (Notion link) required commercial-vs-medicare segmentation."

This is the **product-management ↔ engineering bridge** — neither group has visibility today; brain provides it.

### Connector permission model

Per ADR-0064 audit + ADR-0063 provenance:
- Each connector requires explicit OAuth grant per workspace
- Audit log records every document pulled
- Per-document retention class respects source's policy (Notion docs deleted upstream → brain auto-deletes within 24h)
- PII scrubber from ADR-0064 runs on ingested document content

For privacy-sensitive sources (Slack, email), connectors support `channel_allowlist` / `label_allowlist` so customers limit what's pulled.

---

## File ownership for THIS PR (parallel-safe)

```
company-brain-ai/src/companybrain/connectors/                 # NEW DIRECTORY
company-brain-ai/src/companybrain/connectors/__init__.py
company-brain-ai/src/companybrain/connectors/base.py          # DocumentConnector protocol
company-brain-ai/src/companybrain/connectors/notion.py
company-brain-ai/src/companybrain/connectors/confluence.py
company-brain-ai/src/companybrain/connectors/figma.py
company-brain-ai/src/companybrain/connectors/slack.py
company-brain-ai/src/companybrain/connectors/linear.py
company-brain-ai/src/companybrain/connectors/jira.py
company-brain-ai/src/companybrain/connectors/pdf.py
company-brain-ai/src/companybrain/connectors/loom.py
company-brain-ai/src/companybrain/connectors/email.py
company-brain-ai/src/companybrain/extractors/markdown_to_document.py      # Document content extractor
company-brain-ai/src/companybrain/extractors/pdf_to_markdown.py            # PDF text extraction
company-brain-ai/src/companybrain/pipeline/document_chunker.py             # H2/H3-aware chunking
company-brain-ai/src/companybrain/pipeline/cross_reference_inferrer.py     # M3 inference pass
db/migrations/V18__document_entities.sql                                    # NEW
tests/unit/test_connectors.py
tests/acceptance/test_notion_to_brain_e2e.py
tests/acceptance/test_pdf_extraction.py
```

Append-only edits to:

```
company-brain-ai/src/companybrain/models/entities.py                # add Document, DocumentSection, DOCUMENTS edge
company-brain-ai/src/companybrain/pipeline/orchestrator.py          # invoke ingest connectors before cross_file_pass
company-brain-ai/src/companybrain/api/routes/admin.py               # POST /connectors/{name}/sync
company-brain-ai/src/companybrain/cli.py                            # `brain ingest <source>` command
pyproject.toml                                                        # connector deps (notion-client, atlassian-python-api, slack-sdk, pdfminer.six, etc.)
```

Does NOT touch any file owned by ADR-0064-0069 or 0071.

---

## Acceptance test

```python
async def test_notion_prd_extracted_and_cross_referenced(notion_mock):
    """A Notion PRD that mentions 'lob column' creates a DOCUMENTS edge to
    the DatabaseColumn entity for lob (extracted by ADR-0058)."""
    notion_mock.add_page(
        title="PRD: Lob Reporting Requirements",
        content="The `lob` column on plan_info segments commercial vs medicare per Acme Q3-2024 contract.",
    )
    await run_pipeline_harness(repo="fixtures/network-iq-snapshot", connectors=["notion"])
    edges = await brain.query("DOCUMENTS edges to comp_providers.lob")
    assert any("Lob Reporting Requirements" in e.from_urn for e in edges)


async def test_pdf_uploaded_and_chunked():
    """A 30-page PDF spec becomes one Document + N DocumentSection entities."""
    await brain.connectors.pdf.ingest("fixtures/sample-spec.pdf")
    docs = await brain.query("Document entities source='pdf'")
    assert len(docs) == 1
    sections = await brain.query("DocumentSection entities parent=...")
    assert len(sections) >= 5


async def test_query_surfaces_document_alongside_code():
    """Asking 'why does the lob column exist?' returns BOTH code citations
    AND the Notion PRD that required it."""
    await ingest_demo_fixtures(["notion-prds", "network-iq-code"])
    response = await brain.query("why does the lob column exist?")
    assert any("Notion" in c.source for c in response.citations)
    assert any("plan_info" in c.source for c in response.citations)


async def test_pii_scrubbed_in_slack_messages():
    """A Slack thread with an email address gets the email scrubbed before
    embedding (per ADR-0064)."""
    slack_mock.add_thread(text="Talked to alice@acme.com about the rename")
    await run_connector("slack")
    doc = await brain.query("Document with content containing 'alice'")
    assert "alice@acme.com" not in doc.content_md
    assert "[REDACTED:EMAIL]" in doc.content_md
```

---

## Effort estimate

8 days, parallelisable to 3 days with 4 sessions:

| Workstream | Days |
|---|---|
| Connector framework + 3 priority connectors (Notion + Confluence + PDF) | 3 |
| Slack + Linear + Jira + Figma + Loom + Email connectors | 3 |
| Document chunker + cross-reference inferrer + V18 migration | 1 |
| API endpoints + CLI commands + acceptance | 1 |

For the demo, ship ONLY the 3 priority connectors (Notion + Confluence + PDF). Defer the rest to post-seed.

---

## Demo positioning

If you ship M1+M3 + Notion connector before the demo, the marquee moment becomes:

> *Investor: "What about all the docs in Notion? Engineers spend hours reading those."*
>
> Founder: *"Watch this."* [pulls up brain]
> *"Why does the lob column exist?"*
> [Brain answers in 4 seconds, citing both `plan_info.lob` (DDL) AND the Notion PRD that drove the requirement, with quotes from each.]
> [Investor recognises the magic — code + product knowledge in one query.]

This is the slide #2 wow moment. Worth 2-3 days of effort to land for the seed pitch IF Wave 1 + Wave 2 both finish on time.

---

## Action items

1. [ ] `connectors/base.py` — DocumentConnector protocol + ConnectorScope dataclass.
2. [ ] `connectors/notion.py` — Notion API client; pulls pages from a workspace + database.
3. [ ] `connectors/confluence.py` — Confluence REST client.
4. [ ] `connectors/pdf.py` — local file + URL ingestion; pdfminer.six text extraction.
5. [ ] `connectors/{slack,linear,jira,figma,loom,email}.py` — defer to post-seed if needed.
6. [ ] `extractors/markdown_to_document.py` — Notion/Confluence raw → markdown → Document entity.
7. [ ] `extractors/pdf_to_markdown.py` — PDF → text → markdown chunked by heuristic.
8. [ ] `pipeline/document_chunker.py` — section-aware chunking.
9. [ ] `pipeline/cross_reference_inferrer.py` — M3 inference; runs after both code and document extraction.
10. [ ] V18 migration: `documents`, `document_sections` tables; new `DOCUMENTS` edge type.
11. [ ] `POST /api/v2/connectors/{name}/sync` endpoint.
12. [ ] `brain ingest notion --workspace=X --database=Y` CLI.
13. [ ] PII scrubber wires through (per ADR-0064) on document content before embedding.
14. [ ] Acceptance: 4 tests above PASS.
15. [ ] Telemetry: per-connector `documents_pulled`, `sections_extracted`, `cross_references_emitted`.
