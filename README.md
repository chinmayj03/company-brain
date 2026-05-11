# Company Brain

> *The missing layer between raw company data and reliable AI automation.*

A dependency intelligence platform that maps your codebase, preserves institutional knowledge, and answers "what breaks if I change this?" — across microservices, frontends, databases, and eventually every domain in your company.

---

## What's Running

| Service | Port | Description |
|---------|------|-------------|
| **Frontend** | 5173 | React dashboard — service map, API explorer, Ask |
| **Backend** | 8080 | Java Spring Boot — graph queries, blast radius, annotations |
| **AI Service** | 8000 | Python FastAPI — LLM pipeline, context extraction, NL query |
| **PostgreSQL** | 5432 | Graph store with RLS |
| **Redis** | 6379 | Blast-radius cache |
| **LocalStack** | 4566 | SQS ingestion queue (local AWS emulation) |
| **Ollama** | 11434 | Local LLM inference (OpenAI-compatible API) |

---

## Prerequisites

Run the checker first:
```bash
bash scripts/check-prereqs.sh
```

Or check manually:

| Tool | Required | Install |
|------|----------|---------|
| Docker Desktop | ✅ | [docker.com/products/docker-desktop](https://docker.com/products/docker-desktop) |
| Java 21 | ✅ | `brew install openjdk@21` |
| Node.js 20+ | ✅ | `brew install node` |
| Python 3.11+ | ✅ | `brew install python@3.11` |
| Maven | optional | `./mvnw` auto-downloads Maven 3.9.6 on first run |

---

## First-Time Setup (one command)

```bash
make setup
```

This:
1. Creates `.env` from `.env.example`
2. Starts all Docker infrastructure (Postgres, Redis, LocalStack, Ollama)
3. Pulls `llama3.1:8b` (~4.7 GB — takes a few minutes on first run)

---

## Start the App (three terminals)

Open three terminal tabs from the project root:

**Terminal 1 — Backend (Java API)**
```bash
make backend
# Starts on http://localhost:8080
# First run downloads Maven 3.9.6 automatically (~50 MB)
# First compile takes ~60 seconds; subsequent starts ~5 seconds
```

**Terminal 2 — AI Service (Python)**
```bash
make ai
# Starts on http://localhost:8000
# First run installs Python packages (~2 minutes)
# Swagger UI: http://localhost:8000/docs
```

**Terminal 3 — Frontend (React)**
```bash
make frontend
# Starts on http://localhost:5173
# First run installs npm packages (~30 seconds)
# Open: http://localhost:5173
```

Wait for all three to show "started" or "ready", then open **http://localhost:5173**.

---

## What You'll See

**Service Map** — Search for any service, see its 2-hop dependency graph and blast radius panel.

**API Explorer** — Enter a Git repo URL, trigger the 4-pass LLM extraction pipeline, and watch it build the knowledge graph in real time.

**Ask** — Chat interface grounded in the dependency graph. Try:
- *"What breaks if I rename the amount_cents column?"*
- *"Who owns the payments service?"*
- *"Which frontend components call the charge endpoint?"*

---

## LLM Provider

The app ships defaulting to **Ollama** (local, no API keys needed).

```bash
# Check which models you have
make ollama-models

# Switch to Anthropic Claude (add ANTHROPIC_API_KEY to .env first)
make switch-anthropic

# Switch back to local Ollama
make switch-ollama
```

### Apple Silicon (M1/M2/M3) — Better GPU performance

Ollama runs faster natively than in Docker on Apple Silicon:
```bash
# In a separate terminal:
make ollama-native

# Then update .env:
OLLAMA_HOST=http://localhost:11434

# And comment out the 'ollama' service in docker-compose.infra.yml
```

---

## All Make Commands

```bash
make help         # Show all commands
make up           # Start Docker infrastructure
make down         # Stop Docker infrastructure
make status       # Show service endpoints + active LLM provider
make logs         # Follow infrastructure logs

make backend      # Start Java API (port 8080)
make ai           # Start Python AI service (port 8000)
make frontend     # Start React dashboard (port 5173)

make pull-small   # Pull llama3.1:8b (~4.7 GB, 8 GB RAM)
make pull-deepseek # Pull deepseek-r1:14b (~8 GB, 16 GB RAM)
make ollama-models # List pulled models

make switch-ollama     # Use local Ollama
make switch-anthropic  # Use Anthropic Claude
make switch-openai     # Use OpenAI

make db-shell     # psql into Postgres
make db-reset     # Drop + recreate database
make redis-shell  # redis-cli
```

---

## Supported Languages

The extraction pipeline works for any language — no per-language code paths exist
in orchestrator logic. All framework-specific knowledge lives inside focused LLM
prompts (ADR-0042).

| Language / Runtime | Frameworks detected | Extraction passes |
|---|---|---|
| **Java** | Spring Boot, Hibernate/JPA, jOOQ, Flyway, Liquibase | All 5 passes |
| **Python** | FastAPI, Flask, SQLAlchemy, Alembic, Prisma (Python) | All 5 passes |
| **TypeScript / JavaScript** | Next.js, NestJS, Drizzle ORM, Prisma, Knex | All 5 passes |
| **Ruby** | Rails (test coverage detection) | TestCoveragePass |
| **Go** | Standard `testing` package | TestCoveragePass |
| **.NET / C#** | xUnit, NUnit, MSTest | TestCoveragePass |
| **Other** | Any HTTP/REST/gRPC call patterns | ClientCallPass |

To add a new framework: update the system prompt in the relevant pass file in
`company-brain-ai/src/companybrain/pipeline/passes/`. No orchestrator changes needed.

---

## Environment Variables

Key variables in `.env` (generated from `.env.example`):

```bash
# Which LLM to use
LLM_PROVIDER=ollama             # ollama | anthropic | openai

# Local Ollama (when running in Docker)
OLLAMA_HOST=http://localhost:11434

# API keys (only needed if switching provider)
ANTHROPIC_API_KEY=
OPENAI_API_KEY=

# JWT secret (change in production)
JWT_SECRET=change-me-in-production-minimum-256-bits

# DB (defaults work with docker-compose.infra.yml)
DB_HOST=localhost
DB_PORT=5432
DB_NAME=companybrain
DB_USER=companybrain
DB_PASSWORD=companybrain
```

---

## Project Structure

```
company-brain/
├── company-brain-backend/     # Java 21 · Spring Boot 3.2
│   ├── src/main/java/com/companybrain/
│   │   ├── controller/        # REST endpoints
│   │   ├── service/           # Business logic + blast radius
│   │   ├── model/             # JPA entities (Node, Edge, NodeContext)
│   │   ├── repository/        # Spring Data JPA
│   │   ├── security/          # JWT auth + RLS interceptor
│   │   └── config/            # Redis, DB, security config
│   └── src/main/resources/
│       ├── application.yml    # Spring config
│       └── db/migration/      # Flyway SQL migrations
│
├── company-brain-ai/          # Python 3.11 · FastAPI
│   └── src/companybrain/
│       ├── api/routes/        # pipeline, query, health endpoints
│       ├── pipeline/          # 4-pass LLM extraction
│       ├── collectors/        # Git collector (+ Slack, Zendesk roadmap)
│       ├── llm/               # Provider abstraction (Ollama/Anthropic/OpenAI)
│       └── graph/             # Graph builder (upserts to Postgres)
│
├── company-brain-frontend/    # React 18 · Vite · Tailwind
│   └── src/
│       ├── pages/             # Dashboard, ApiExplorer, QueryPage
│       ├── components/        # DependencyGraph, BlastRadiusPanel, AnnotationEditor
│       └── api/client.js      # Axios API client
│
├── docker-compose.infra.yml   # Postgres, Redis, LocalStack, Ollama
├── Makefile                   # All dev commands
├── .env.example               # Config template
└── docs/                      # Architecture docs and ADRs
```

---

## Troubleshooting

**Backend fails to start — Flyway migration error**
```bash
make db-reset   # Drop and recreate the DB — Flyway will re-run migrations
```

**Backend fails — SQS connection error**
```
# LocalStack must be running and healthy first
make up
# Wait for: cb-localstack  Up (healthy)
```

**AI service: `OLLAMA_HOST connection refused`**
```bash
# Make sure Ollama is running and healthy
docker ps | grep cb-ollama
# If health: starting — wait 20–30 seconds and retry
```

**Frontend: blank screen / API errors**
```bash
# Backend must be running first
make backend    # wait for "Started CompanyBrainApplication"
make ai         # wait for "Application startup complete"
make frontend   # then start frontend
```

**Ollama: model not found**
```bash
make pull-small   # pulls llama3.1:8b — required before first pipeline run
```

**`make backend` hangs on first run**
```
# mvnw is downloading Maven 3.9.6 (~50 MB) — wait 1–2 minutes
# Subsequent runs skip the download
```

---

## Extraction model: chunked, resumable, language-agnostic

As of ADR-0044, the extraction pipeline processes files of **any size** correctly.

**How it works:**
1. A tree-sitter–based code chunker splits every source file into individual
   method/declaration chunks, each with class header + import context.
2. Chunks are written to a Postgres queue (`extraction_queue`). Workers claim
   one chunk at a time via `SELECT … FOR UPDATE SKIP LOCKED`, so multiple
   coroutines run in parallel without duplicate work.
3. Each LLM call is bounded to `max_tokens=600` — JSON output is never cut.
4. A deterministic merger deduplicates entities that appear in multiple chunks
   and resolves edge targets to canonical URNs.

**Environment flags:**

| Variable | Default | Effect |
|----------|---------|--------|
| `BRAIN_USE_CHUNK_QUEUE` | `true` | Enable chunked extraction (ADR-0044) |
| `BRAIN_LEGACY_EXTRACT` | `false` | Force the old per-file path (escape hatch) |
| `BRAIN_JOB_BUDGET_USD` | `0.50` | Abort a pipeline run if cost exceeds this |

**Languages supported:** Java, Python, TypeScript/TSX, JavaScript, Go, Kotlin, Rust, Ruby (via tree-sitter grammars).
