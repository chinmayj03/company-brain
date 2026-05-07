# ══════════════════════════════════════════════════════════════════════════════
# Company Brain — Makefile
# Run `make help` to see all commands.
# ══════════════════════════════════════════════════════════════════════════════

.PHONY: help setup up down logs ps status \
        pull-models pull-small pull-deepseek ollama-models ollama-native \
        switch-ollama switch-anthropic switch-openai switch-groq switch-openrouter \
        backend ai mcp frontend venv \
        db-shell db-reset redis-shell \
        langfuse qdrant neo4j-shell neo4j-reset \
        brain-index brain-query brain-blast

INFRA_COMPOSE = docker compose -f docker-compose.infra.yml
ENV_FILE      = company-brain-ai/.env
GREEN  = \033[0;32m
YELLOW = \033[0;33m
CYAN   = \033[0;36m
RESET  = \033[0m

help: ## Show this help
	@echo ""
	@echo "$(CYAN)Company Brain — Developer Commands$(RESET)"
	@echo ""
	@awk 'BEGIN {FS = ":.*##"} /^[a-zA-Z_-]+:.*##/ {printf "  $(GREEN)%-22s$(RESET) %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@echo ""

# ── First-time setup ──────────────────────────────────────────────────────────

setup: ## First-time setup: create .env, start infra, pull smallest model
	@[ -f $(ENV_FILE) ] || (cp company-brain-ai/.env.example $(ENV_FILE) && echo "$(GREEN)✓ Created $(ENV_FILE)$(RESET)")
	@make up
	@make pull-small
	@echo ""
	@echo "$(GREEN)━━━ Setup complete ━━━$(RESET)"
	@echo "  make backend    — start Java API       (port 8080)"
	@echo "  make ai         — start Python AI      (port 8000)"
	@echo "  make frontend   — start React UI       (port 5173)"
	@echo ""

# ── Infrastructure ────────────────────────────────────────────────────────────

up: ## Start all infrastructure (postgres, redis, localstack, ollama)
	@echo "$(CYAN)Starting infrastructure...$(RESET)"
	$(INFRA_COMPOSE) up -d
	@make status

down: ## Stop all infrastructure
	$(INFRA_COMPOSE) down

logs: ## Follow logs from all infra containers
	$(INFRA_COMPOSE) logs -f

ps: ## Show running containers
	$(INFRA_COMPOSE) ps

status: ## Show service endpoints and active LLM provider
	@echo ""
	@echo "$(CYAN)Infrastructure$(RESET)"
	@echo "  PostgreSQL  → localhost:5432  (db: companybrain / user: companybrain)"
	@echo "  Redis       → localhost:6379"
	@echo "  LocalStack  → localhost:4566  (SQS)"
	@echo "  Ollama      → localhost:11434 (OpenAI-compatible)"
	@echo "  Qdrant      → localhost:6333  (vector store / code embeddings)"
	@echo "  Neo4j       → localhost:7474  (graph / bolt: 7687)"
	@echo "  Langfuse    → localhost:3001  (LLM observability)"
	@echo ""
	@echo "$(CYAN)Application$(RESET)"
	@echo "  Backend     → localhost:8080  (make backend)"
	@echo "  AI Service  → localhost:8000  (make ai)"
	@echo "  MCP Server  → localhost:9000  (make mcp)"
	@echo "  Frontend    → localhost:5173  (make frontend)"
	@echo ""
	@echo "$(CYAN)LLM Provider$(RESET)"
	@grep "^LLM_PROVIDER" $(ENV_FILE) 2>/dev/null || echo "  LLM_PROVIDER=groq (default)"
	@grep "^GROQ_MODEL_FAST\|^GROQ_MODEL_REASONING\|^GROQ_MODEL_SYNTHESIS" $(ENV_FILE) 2>/dev/null | sed 's/^/  /' || true
	@echo ""

# ── Run application services (dev mode) ──────────────────────────────────────

JAVA21_HOME := $(shell /usr/libexec/java_home -v 21 2>/dev/null)
BUN         := $(shell command -v bun 2>/dev/null || echo "$(HOME)/.bun/bin/bun")

trpc: ## Start tRPC graph API (port 8090) — serves Neo4j queries to frontend + MCP
	@echo "$(CYAN)Starting tRPC API on port 8090...$(RESET)"
	@if [ ! -x "$(BUN)" ]; then \
		echo "$(YELLOW)Bun not found. Install with:$(RESET)"; \
		echo "  curl -fsSL https://bun.sh/install | bash"; \
		echo "  then restart your terminal and run: make trpc"; \
		exit 1; \
	fi
	cd apps/api && $(BUN) run src/index.ts

backend: ## Start Java Spring Boot backend (port 8080)
	@echo "$(CYAN)Starting backend...$(RESET)"
	@if [ -z "$(JAVA21_HOME)" ]; then \
		echo "$(RED)Java 21 not found. Install with: brew install openjdk@21$(RESET)"; exit 1; \
	fi
	cd company-brain-backend && JAVA_HOME="$(JAVA21_HOME)" ./mvnw spring-boot:run -Dspring-boot.run.profiles=dev

PYTHON      := $(shell command -v python3.11 2>/dev/null || command -v python3.12 2>/dev/null || command -v python3 2>/dev/null)
AI_VENV     := company-brain-ai/.venv
AI_PYTHON   := $(AI_VENV)/bin/python
AI_UVICORN  := $(AI_VENV)/bin/uvicorn

$(AI_VENV): ## Create venv for the AI service (run once)
	@echo "$(CYAN)Creating Python venv in $(AI_VENV)...$(RESET)"
	$(PYTHON) -m venv $(AI_VENV)
	$(AI_PYTHON) -m pip install --upgrade pip -q
	cd company-brain-ai && $(AI_PYTHON) -m pip install -e ".[dev]" -q
	@echo "$(GREEN)✓ venv ready$(RESET)"

venv: $(AI_VENV) ## Create (or refresh) the AI service venv

ai: $(AI_VENV) ## Start Python FastAPI AI service (port 8000)
	@echo "$(CYAN)Starting AI service...$(RESET)"
	@echo "LLM Provider: $$(grep '^LLM_PROVIDER' $(ENV_FILE) 2>/dev/null || echo 'groq (default)')"
	cd company-brain-ai && \
		.venv/bin/uvicorn companybrain.api.main:app \
		--host 0.0.0.0 --port 8000 --reload --env-file .env

mcp: $(AI_VENV) ## Start MCP server (HTTP+SSE, port 9000) — connects Claude Code / Cursor to company-brain
	@echo "$(CYAN)Starting MCP server...$(RESET)"
	@echo "Backend URL: $$(grep '^BACKEND_URL' $(ENV_FILE) 2>/dev/null || echo 'http://localhost:8080 (default)')"
	cd company-brain-ai && \
		.venv/bin/uvicorn companybrain.mcp.server:asgi_app \
		--host 0.0.0.0 --port 9000 --reload --env-file .env

mcp-stdio: $(AI_VENV) ## Run MCP server over stdio (on-prem agent mode — for direct Claude Code integration)
	@echo "$(CYAN)Starting MCP server (stdio)...$(RESET)"
	cd company-brain-ai && .venv/bin/python -m companybrain.mcp.server --stdio

frontend: ## Start React frontend dev server (port 5173)
	@echo "$(CYAN)Starting frontend...$(RESET)"
	cd company-brain-frontend && npm install && npm run dev

# ── Model management ──────────────────────────────────────────────────────────

pull-models: ## Pull all recommended models (needs 16+ GB RAM, ~20 GB download)
	$(INFRA_COMPOSE) --profile pull-models up model-puller

pull-small: ## Pull only llama3.1:8b — works on 8 GB RAM (~4.7 GB download)
	@echo "$(CYAN)Pulling llama3.1:8b...$(RESET)"
	docker exec cb-ollama ollama pull llama3.1:8b
	@echo "$(GREEN)✓ llama3.1:8b ready$(RESET)"

pull-deepseek: ## Pull deepseek-r1:14b — best local reasoning model (~8 GB download)
	@echo "$(CYAN)Pulling deepseek-r1:14b...$(RESET)"
	docker exec cb-ollama ollama pull deepseek-r1:14b
	@echo "$(GREEN)✓ deepseek-r1:14b ready$(RESET)"

ollama-models: ## List models currently pulled in Ollama
	@curl -s http://localhost:11434/api/tags | python3 -c \
		"import sys,json; models=json.load(sys.stdin).get('models',[]); \
		[print(f'  {m[\"name\"]:40s} {round(m[\"size\"]/1e9,1)} GB') for m in models] \
		if models else print('  No models pulled yet. Run: make pull-small')"

ollama-native: ## Run Ollama natively on Apple Silicon (better GPU perf than Docker)
	@echo "$(YELLOW)Starting Ollama natively (uses Apple Metal GPU)$(RESET)"
	@echo "After this, update .env: OLLAMA_HOST=http://host.docker.internal:11434"
	@echo "And comment out the 'ollama' service in docker-compose.infra.yml"
	ollama serve &

# ── LLM provider switching ────────────────────────────────────────────────────

switch-ollama: ## Switch to local Ollama (no API key needed)
	@sed -i.bak 's/^LLM_PROVIDER=.*/LLM_PROVIDER=ollama/' $(ENV_FILE) && rm -f $(ENV_FILE).bak
	@echo "$(GREEN)✓ LLM_PROVIDER=ollama$(RESET)"

switch-anthropic: ## Switch to Anthropic Claude API (requires ANTHROPIC_API_KEY in .env)
	@sed -i.bak 's/^LLM_PROVIDER=.*/LLM_PROVIDER=anthropic/' $(ENV_FILE) && rm -f $(ENV_FILE).bak
	@echo "$(GREEN)✓ LLM_PROVIDER=anthropic$(RESET)"
	@grep -q "^ANTHROPIC_API_KEY=$$" $(ENV_FILE) 2>/dev/null && \
		echo "$(YELLOW)⚠  Set ANTHROPIC_API_KEY in $(ENV_FILE)$(RESET)" || true

switch-openai: ## Switch to OpenAI API (requires OPENAI_API_KEY in .env)
	@sed -i.bak 's/^LLM_PROVIDER=.*/LLM_PROVIDER=openai/' $(ENV_FILE) && rm -f $(ENV_FILE).bak
	@echo "$(GREEN)✓ LLM_PROVIDER=openai$(RESET)"
	@grep -q "^OPENAI_API_KEY=$$" $(ENV_FILE) 2>/dev/null && \
		echo "$(YELLOW)⚠  Set OPENAI_API_KEY in $(ENV_FILE)$(RESET)" || true

switch-groq: ## Switch to Groq API — free, 500–1000 tok/s (requires GROQ_API_KEY in .env)
	@sed -i.bak 's/^LLM_PROVIDER=.*/LLM_PROVIDER=groq/' $(ENV_FILE) && rm -f $(ENV_FILE).bak
	@echo "$(GREEN)✓ LLM_PROVIDER=groq$(RESET)"
	@echo "  FAST      → llama-3.1-8b-instant"
	@echo "  BALANCED  → meta-llama/llama-4-scout-17b-16e-instruct"
	@echo "  SYNTHESIS → openai/gpt-oss-120b"
	@echo "  REASONING → qwen/qwen3-32b"
	@grep -q "^GROQ_API_KEY=gsk_" $(ENV_FILE) 2>/dev/null || \
		echo "$(YELLOW)⚠  Set GROQ_API_KEY in $(ENV_FILE)  →  https://console.groq.com/keys$(RESET)"

switch-openrouter: ## Switch to OpenRouter (free tier, 30+ models, requires OPENROUTER_API_KEY in .env)
	@sed -i.bak 's/^LLM_PROVIDER=.*/LLM_PROVIDER=openrouter/' $(ENV_FILE) && rm -f $(ENV_FILE).bak
	@echo "$(GREEN)✓ LLM_PROVIDER=openrouter$(RESET)"
	@grep -q "^OPENROUTER_API_KEY=sk-or-" $(ENV_FILE) 2>/dev/null || \
		echo "$(YELLOW)⚠  Set OPENROUTER_API_KEY in $(ENV_FILE)  →  https://openrouter.ai/keys$(RESET)"

# ── Database utilities ────────────────────────────────────────────────────────

db-shell: ## Open psql shell in the Postgres container
	docker exec -it cb-postgres psql -U companybrain -d companybrain

db-reset: ## Drop and recreate the database (WARNING: destroys all data)
	@echo "$(YELLOW)Resetting database...$(RESET)"
	docker exec cb-postgres psql -U companybrain \
		-c "DROP DATABASE IF EXISTS companybrain;" \
		-c "CREATE DATABASE companybrain;"
	@echo "$(GREEN)✓ Database reset — Flyway will re-run migrations on next backend start$(RESET)"

redis-shell: ## Open redis-cli in the Redis container
	docker exec -it cb-redis redis-cli

# ── Observability ─────────────────────────────────────────────────────────────

langfuse: ## Open Langfuse LLM observability dashboard (http://localhost:3001)
	@echo "$(CYAN)Langfuse → http://localhost:3001$(RESET)"
	@echo "  First run: create account at http://localhost:3001"
	@echo "  Then: copy Public/Secret keys into $(ENV_FILE)"
	@open http://localhost:3001 2>/dev/null || true

# ── Graph database ────────────────────────────────────────────────────────────

neo4j-shell: ## Open Neo4j browser (http://localhost:7474)
	@echo "$(CYAN)Neo4j Browser → http://localhost:7474$(RESET)"
	@echo "  Connect with: bolt://localhost:7687  user: neo4j  pass: password"
	@open http://localhost:7474 2>/dev/null || true

neo4j-reset: ## Wipe Neo4j data volume (WARNING: destroys all graph data)
	@echo "$(YELLOW)Stopping Neo4j and wiping data volume...$(RESET)"
	$(INFRA_COMPOSE) stop neo4j
	docker volume rm cb-neo4j-data cb-neo4j-logs 2>/dev/null || true
	$(INFRA_COMPOSE) up -d neo4j
	@echo "$(GREEN)✓ Neo4j reset — wait ~30s for it to be ready$(RESET)"

qdrant: ## Open Qdrant dashboard (http://localhost:6333/dashboard)
	@echo "$(CYAN)Qdrant → http://localhost:6333/dashboard$(RESET)"
	@open http://localhost:6333/dashboard 2>/dev/null || true

# ── Brain CLI (ADR-0016) ──────────────────────────────────────────────────────

brain-index: ## Run whole-repo extraction: make brain-index REPO=./path/to/repo
	cd company-brain-ai && python -m companybrain.cli index $(REPO)

brain-query: ## Query the brain: make brain-query Q="payment processing" REPO=./path/to/repo
	cd company-brain-ai && python -m companybrain.cli query "$(Q)" --repo $(REPO)

brain-blast: ## Blast radius: make brain-blast URN="urn:cb:dev:code:monorepo:component:MyService"
	cd company-brain-ai && python -m companybrain.cli blast-radius "$(URN)"

