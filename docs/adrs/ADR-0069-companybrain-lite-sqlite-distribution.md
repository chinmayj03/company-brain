# ADR-0069 — `companybrain-lite` SQLite-Default Distribution (developer-friendly low-friction distro)

**Status:** Proposed
**Date:** 2026-05-11
**Inspired by:** ContextDB's single-package `pip install pycontextdb` shape (Apache 2.0; concept adopted, code is our own per LEGAL-CONTEXTDB-INTEGRATION.md)
**Sequenced with:** depends on ADR-0048 (caching), ADR-0050 (recovery), ADR-0058 (schema awareness — works on basic dialects), ADR-0064 (PII layer); independent of others. **Optional for seed; recommended for Series B (bottom-up developer market unlock).**

---

## Context

Today's brain is **infrastructure-heavy**: Docker compose + Spring Boot Java backend + Python FastAPI + Bun TypeScript cb-api + Vite frontend + 5 storage layers (Postgres + Neo4j + Qdrant + Redis + LocalStack S3 + JSON brain). 30+ minutes of setup before a developer sees the first response.

**This is a barrier to evaluation.** Every developer who hears about company-brain at a conference, on Twitter, or in a Slack channel has a 30-minute friction wall before the "wow" moment. ContextDB does this differently — `pip install pycontextdb`, three lines, working. Their adoption velocity reflects it (PyPI downloads + GitHub stars are proxies; both growing fast).

**Strategic implication**: today we're a top-down enterprise sales motion only. We have NO bottom-up developer adoption. That's fine for Series A — but for Series B we need both wedges: enterprise contracts AND viral developer evangelism. The latter is impossible without a low-friction distribution.

The architecture supports this. Postgres can be replaced with SQLite for ≤10K entities. Qdrant can be replaced with NumPy + DuckDB vector search. Neo4j can be replaced with NetworkX in-memory or SQLite recursive CTEs. The agentic harness (ADR-0051) is optional. Multi-tenancy is optional in single-user mode. **What's missing isn't capability — it's a distribution.**

---

## Decision

Ship `companybrain-lite` as a **single-package Python distribution** with SQLite + DuckDB defaults, shipped via PyPI. **Same code paths as production** (no separate "lite" implementation; it's the same brain with different storage backends), but trades scale + multi-tenancy for radical simplicity.

### What lite IS

- One pip install: `pip install companybrain-lite` (~150MB with embedded models in optional extras)
- One Python import: `from companybrain import Brain`
- Three-line minimum: `b = Brain(repo="./my-repo"); await b.extract(); print(await b.query("..."))`
- Single-user (no workspace_id complexity required; defaults to `local`)
- All data in `./.brain/` (SQLite + Parquet for vectors)
- Local-LLM-friendly: works with Ollama out of the box (no Anthropic key required for evaluation)
- Eval-mode: includes 3 pre-extracted demo repos so users see the wow moment immediately
- ALL the extraction quality of full brain (the ContextAgent, RecoveryWrapper, schema awareness, BC v2 — all included)

### What lite IS NOT

- NOT multi-tenant (no org/workspace RBAC)
- NOT scalable beyond ~10K entities (SQLite + NumPy vector search slow above that)
- NOT highly available (single-node)
- NOT enterprise (no SSO, no audit chain, no compliance reports)
- NOT for production teams of 50+ engineers — they need full brain
- NOT a separate codebase — same Python package; lite is a runtime mode

### Storage backend swap

The brain already has a storage abstraction (`store/base.py`). Lite mode binds to:

| Production layer | Lite replacement | When it breaks |
|---|---|---|
| Postgres `nodes/edges/edge_events/node_context` | SQLite (single file `.brain/brain.db`) | > 10K entities; no concurrent writes |
| Neo4j graph | SQLite recursive CTEs OR NetworkX in-memory | > 50K edges; complex graph queries slow |
| Qdrant vector store | NumPy + DuckDB cosine search | > 100K vectors; no ANN — full scan |
| Redis cache/queue | Python in-memory dict + asyncio.Queue | Single-process only |
| LocalStack S3 | local filesystem `.brain/artifacts/` | No remote sharing |
| Anthropic Haiku/Sonnet | Ollama Llama 3.1 8B (default for lite) OR Anthropic key if provided | Quality dip without Anthropic; documented |

Each backend is ~100-300 LOC of glue. Already partially in place via the existing storage abstraction.

### Distribution shape

```python
# pyproject.toml entry
[project]
name = "companybrain-lite"
version = "0.1.0"

# Default install — minimal
dependencies = [
    "pydantic>=2",
    "aiosqlite>=0.19",
    "numpy>=1.24",
    "duckdb>=0.10",
    "tree-sitter>=0.20",
    "tree-sitter-languages>=1.10",
    "anthropic>=0.39",        # optional at runtime; lite works without
    "httpx>=0.27",
]

[project.optional-dependencies]
ollama = ["ollama>=0.1"]
embeddings = ["sentence-transformers>=2.0"]   # local embedding model
all = ["companybrain-lite[ollama,embeddings]"]
```

Install paths:
- `pip install companybrain-lite` — minimal; uses Anthropic if `ANTHROPIC_API_KEY` set, else asks user
- `pip install companybrain-lite[ollama]` — local-only with Ollama for LLM
- `pip install companybrain-lite[embeddings]` — local sentence-transformers (no OpenAI embedding key required)
- `pip install companybrain-lite[all]` — fully self-contained

### CLI shape (mirrors lite's Python API)

```bash
# Index a repo
brain index ./my-repo

# Query
brain query "what does foo do?"

# Run a quick demo on a pre-extracted public repo
brain demo

# Optional: serve as MCP over stdio (for Claude Code / Cursor / etc. integration)
brain serve

# Upgrade to full distribution (offered at the end of every CLI run when
# repo size exceeds threshold)
brain upgrade --info
```

### Same-code, different-runtime guarantee

The lite distribution is **NOT a fork**. It's the same `companybrain` Python package with:

1. A `LITE_MODE = True` config flag set at import time
2. Storage factory returns SQLite/DuckDB/NumPy backends instead of Postgres/Neo4j/Qdrant
3. Optional features (Java backend coordination, audit chain, multi-tenant RBAC) are no-ops in lite mode
4. Distribution-wise, lite ships only the Python package + minimal deps; full distribution adds Docker compose + Java backend + Bun cb-api + frontend

This means:
- Bug fixes ship to both lite and full at the same time
- Feature parity at the extraction level is automatic (same ContextAgent, same prompts)
- Customer can `brain index` on lite, then upgrade to full and import the existing `.brain.db` (migration path is just SQLite → Postgres dump)

---

## Strategic positioning

**For developers**: "Try the brain on your repo in 60 seconds. No infrastructure required. Decide if you need full brain after."

**For VPs of Eng (enterprise buyers)**: "Your engineers can prototype with `companybrain-lite` for free; when they bring it to you for a 50-engineer rollout, you upgrade to full brain with multi-tenant RBAC + audit + compliance."

**Pricing model**: lite is free + open-source (Apache 2.0). Full brain is commercial. Bottom-up evangelism feeds top-down enterprise sale.

**This is the Pinecone vs pgvector vs Qdrant playbook**, the LangSmith vs free-LangChain-tracing playbook, the Sentry vs free-OSS-Sentry-server playbook. Every dev-tool unicorn has a freemium open-source distribution AND a paid enterprise product. We don't have either yet on the dev side.

---

## File ownership for THIS PR (parallel-safe with 0055-0068)

```
company-brain-ai/src/companybrain/store/sqlite_backend.py             # NEW — Postgres → SQLite
company-brain-ai/src/companybrain/store/duckdb_vector_backend.py      # NEW — Qdrant → DuckDB
company-brain-ai/src/companybrain/store/networkx_graph_backend.py     # NEW — Neo4j → NetworkX
company-brain-ai/src/companybrain/store/local_artifact_backend.py     # NEW — S3 → local FS
company-brain-ai/src/companybrain/lite/                                # NEW DIRECTORY
company-brain-ai/src/companybrain/lite/__init__.py
company-brain-ai/src/companybrain/lite/brain.py                        # the public Brain class for lite mode
company-brain-ai/src/companybrain/lite/cli.py                          # `brain` CLI entry point for lite
company-brain-ai/src/companybrain/lite/demo_repos.py                   # bundled pre-extracted demo content
company-brain-ai/src/companybrain/lite/upgrade_advisor.py              # detects when user should move to full
company-brain-ai/src/companybrain/lite/migration.py                    # SQLite → Postgres dump for upgrade
companybrain-lite/                                                       # NEW SEPARATE PYPI PACKAGE
companybrain-lite/pyproject.toml                                        # depends on companybrain-ai with [lite] extra
companybrain-lite/README.md
companybrain-lite/LICENSE                                               # Apache 2.0
companybrain-lite/examples/{quickstart.py, with_ollama.py, with_anthropic.py, mcp_serve.py}
tests/lite/test_lite_extract_query.py                                    # NEW
tests/lite/test_lite_to_full_migration.py                                # NEW
tests/lite/test_lite_runs_without_anthropic_key.py                       # NEW
```

Append-only edits to:

```
company-brain-ai/src/companybrain/store/factory.py    # branch on LITE_MODE → return lite backends
company-brain-ai/src/companybrain/config.py           # LITE_MODE flag; lite-specific defaults
company-brain-ai/src/companybrain/api/routes/*.py     # no-op lite-incompatible features (audit, multi-tenant) under LITE_MODE
```

Does NOT touch any file owned by ADR-0055-0068.

---

## Acceptance test

```python
async def test_lite_three_line_quickstart():
    """The README example must actually work."""
    brain = Brain(repo="fixtures/sample-spring", lite_mode=True)
    await brain.extract()
    answer = await brain.query("what is this repo about?")
    assert len(answer.summary_md) > 50
    assert answer.confidence > 0.5


async def test_lite_uses_sqlite_not_postgres():
    """In lite mode, no Postgres connection is opened."""
    brain = Brain(repo="...", lite_mode=True)
    await brain.extract()
    assert (Path(".brain/brain.db")).exists()
    assert no_postgres_connection_was_made()


async def test_lite_works_without_anthropic_key(monkeypatch):
    """When ANTHROPIC_API_KEY is unset and ollama=True is passed, lite uses Ollama."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    brain = Brain(repo="...", lite_mode=True, llm_backend="ollama")
    await brain.extract()
    answer = await brain.query("...")
    # Lower quality acceptable; just don't crash
    assert answer is not None


async def test_lite_demo_command_works_offline():
    """`brain demo` shows pre-extracted content with no network calls."""
    result = subprocess.run(["brain", "demo"], capture_output=True)
    assert result.returncode == 0
    assert "Stripe" in result.stdout or "Spring" in result.stdout


async def test_lite_to_full_migration():
    """A lite .brain/brain.db can be exported to Postgres for full brain."""
    # Create lite brain
    brain = Brain(repo="...", lite_mode=True)
    await brain.extract()
    # Migrate
    await migration.lite_to_postgres(
        sqlite_path=".brain/brain.db",
        postgres_url=test_postgres_url,
    )
    # Now full brain can use it
    full_brain = Brain(repo="...", lite_mode=False)
    answer = await full_brain.query("...")
    assert answer is not None


async def test_upgrade_advisor_triggers_at_threshold():
    """When entity count > 8000, lite logs a notice suggesting upgrade."""
    brain = Brain(repo="fixtures/very-large", lite_mode=True)
    with capture_logs() as logs:
        await brain.extract()
    assert any("consider upgrading to full brain" in log for log in logs)
```

---

## Effort estimate

10 days (2 working weeks), parallelisable to 5 days with 2 sessions:

| Workstream | Days |
|---|---|
| 4 lite-backend modules (SQLite + DuckDB + NetworkX + local FS) | 3 |
| Lite Brain wrapper + CLI | 2 |
| Pre-extracted demo bundle (3 repos: spring-petclinic, FastAPI sample, Cursor docs) | 2 |
| Migration tool (lite → full) | 1 |
| companybrain-lite package + PyPI publishing pipeline | 1 |
| Documentation + quickstart examples + landing page | 1 |

---

## Strategic milestones (when to invest in this)

| Phase | What you have | Invest in lite? |
|---|---|---|
| Pre-seed | <3 design partners, no public traction | NO — focus on enterprise demo |
| Seed close to Series A (~6 months in) | 5-15 paying logos | MAYBE — if dev-evangelism becomes a top-3 GTM lever |
| Post-Series A | Product-market fit established | YES — bottom-up funnel becomes leading indicator for Series B |
| Series B+ | Multi-million ARR | MUST — defending a $1B+ valuation requires both motions |

For the seed pitch itself: **don't ship lite before seed close**. The seed-pitch demo is enterprise-shaped. Lite is a Series-A-and-beyond unlock.

---

## Action items

1. [ ] `store/sqlite_backend.py` — implements `BrainStore` protocol via aiosqlite.
2. [ ] `store/duckdb_vector_backend.py` — implements vector ops via DuckDB cosine.
3. [ ] `store/networkx_graph_backend.py` — implements graph ops via NetworkX in-memory.
4. [ ] `store/local_artifact_backend.py` — implements artifact storage via local FS.
5. [ ] `store/factory.py` — `if LITE_MODE: return lite_backends; else: return prod_backends`.
6. [ ] `lite/brain.py` — public `Brain` class with simplified API.
7. [ ] `lite/cli.py` — `brain index/query/demo/serve/upgrade` commands.
8. [ ] `lite/demo_repos.py` — bundled pre-extracted brains for 3 demo repos.
9. [ ] `lite/upgrade_advisor.py` — detect outgrowing-lite signals.
10. [ ] `lite/migration.py` — SQLite → Postgres dump.
11. [ ] `companybrain-lite` PyPI package (separate distribution that re-exports the above).
12. [ ] Acceptance: 6 tests above PASS.
13. [ ] Telemetry: opt-in usage analytics (lite-specific) on entity count, query count, repo language distribution → tells us when users would naturally upgrade.
14. [ ] Landing page: simple HTML at brain.so/lite (or wherever) with the 3-line quickstart + Loom demo + "upgrade to full" CTA.
