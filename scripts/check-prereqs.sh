#!/usr/bin/env bash
# check-prereqs.sh — Verify all prerequisites for Company Brain dev environment.
# Run from the project root: bash scripts/check-prereqs.sh

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
CYAN='\033[0;36m'
RESET='\033[0m'

ok()   { echo -e "  ${GREEN}✓${RESET}  $1"; }
fail() { echo -e "  ${RED}✗${RESET}  $1"; FAILED=1; }
warn() { echo -e "  ${YELLOW}⚠${RESET}  $1"; }
info() { echo -e "  ${CYAN}→${RESET}  $1"; }

FAILED=0

echo ""
echo -e "${CYAN}Company Brain — Prerequisite Check${RESET}"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# ── Docker ─────────────────────────────────────────────────────────────────
echo ""
echo "Docker"

if command -v docker &>/dev/null; then
  DOCKER_VERSION=$(docker --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)
  ok "Docker found (version $DOCKER_VERSION)"
else
  fail "Docker not found. Install Docker Desktop: https://docker.com/products/docker-desktop"
fi

if docker info &>/dev/null 2>&1; then
  ok "Docker daemon is running"
else
  fail "Docker daemon is not running. Start Docker Desktop."
fi

# Check infrastructure containers
for container in cb-postgres cb-redis cb-localstack cb-ollama; do
  STATUS=$(docker inspect --format='{{.State.Health.Status}}' "$container" 2>/dev/null || echo "not found")
  if [ "$STATUS" = "healthy" ]; then
    ok "$container is healthy"
  elif [ "$STATUS" = "starting" ]; then
    warn "$container is starting (wait 30s and retry)"
  elif [ "$STATUS" = "not found" ]; then
    warn "$container not running — run: make up"
  else
    warn "$container status: $STATUS"
  fi
done

# ── Java ───────────────────────────────────────────────────────────────────
echo ""
echo "Java"

# Check if Java 21 is available via java_home (doesn't need to be on PATH)
JAVA21_PATH=$(/usr/libexec/java_home -v 21 2>/dev/null || echo "")
if [ -n "$JAVA21_PATH" ] && [ -x "$JAVA21_PATH/bin/java" ]; then
  ok "Java 21 found at $JAVA21_PATH (used by make backend)"
else
  # Fallback: check common Temurin/OpenJDK install locations
  for jdir in \
    /Library/Java/JavaVirtualMachines/temurin-21.jdk/Contents/Home \
    /Library/Java/JavaVirtualMachines/openjdk-21.jdk/Contents/Home \
    /opt/homebrew/opt/openjdk@21; do
    if [ -x "$jdir/bin/java" ]; then
      JAVA21_PATH=$jdir
      ok "Java 21 found at $JAVA21_PATH"
      break
    fi
  done
fi

if [ -z "$JAVA21_PATH" ]; then
  SYSTEM_JAVA=$(java -version 2>&1 | grep -oE '"[0-9]+' | tr -d '"' | head -1 2>/dev/null || echo "none")
  fail "Java 21 not found (system Java: $SYSTEM_JAVA). Install: brew install --cask temurin@21"
  info "make backend sets JAVA_HOME automatically — no PATH change needed"
fi

# Maven wrapper
if [ -f "company-brain-backend/mvnw" ]; then
  ok "Maven wrapper (mvnw) present — no system Maven required"
else
  if command -v mvn &>/dev/null; then
    MVN_VERSION=$(mvn --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -1)
    ok "System Maven $MVN_VERSION found"
  else
    fail "mvnw not found and no system Maven. Run: make setup (re-generates mvnw)"
  fi
fi

# ── Node.js ────────────────────────────────────────────────────────────────
echo ""
echo "Node.js"

if command -v node &>/dev/null; then
  NODE_VERSION=$(node --version | tr -d 'v' | cut -d. -f1)
  if [ "$NODE_VERSION" -ge 20 ] 2>/dev/null; then
    ok "Node.js v$(node --version | tr -d 'v') found (20+ required)"
  else
    fail "Node.js v$(node --version | tr -d 'v') found — v20+ required. Install: brew install node"
  fi
else
  fail "Node.js not found. Install: brew install node"
fi

if command -v npm &>/dev/null; then
  ok "npm $(npm --version) found"
else
  fail "npm not found (should ship with Node.js)"
fi

# ── Python ─────────────────────────────────────────────────────────────────
echo ""
echo "Python"

PYTHON_CMD=""
# Check common locations including Homebrew on Apple Silicon and Intel
for cmd in \
  python3.11 python3.12 python3.13 \
  /opt/homebrew/bin/python3.11 \
  /opt/homebrew/bin/python3.12 \
  /usr/local/bin/python3.11 \
  python3; do
  if [ -x "$(command -v "$cmd" 2>/dev/null || echo "$cmd")" ]; then
    PY_MINOR=$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+' | head -1 | cut -d. -f2)
    if [ "${PY_MINOR:-0}" -ge 11 ] 2>/dev/null; then
      PYTHON_CMD=$cmd
      break
    fi
  fi
done

if [ -n "$PYTHON_CMD" ]; then
  ok "Python $("$PYTHON_CMD" --version) found at $PYTHON_CMD"
else
  fail "Python 3.11+ not found. Install: brew install python@3.11"
  info "After install, /opt/homebrew/bin/python3.11 should exist"
fi

# ── Ollama models ──────────────────────────────────────────────────────────
echo ""
echo "Ollama"

if docker exec cb-ollama ollama list &>/dev/null 2>&1; then
  MODELS=$(docker exec cb-ollama ollama list 2>/dev/null | tail -n +2 | awk '{print $1}')
  if echo "$MODELS" | grep -q "llama3.1:8b"; then
    ok "llama3.1:8b is pulled (minimum model — used for FAST role)"
  else
    warn "llama3.1:8b not pulled yet — run: make pull-small"
  fi
  if echo "$MODELS" | grep -q "deepseek"; then
    ok "DeepSeek model found (used for SYNTHESIS/REASONING roles)"
  else
    warn "No DeepSeek model — pipeline will use llama3.1:8b for all roles (lower quality)"
    info "For better results run: make pull-deepseek"
  fi
else
  warn "Ollama not accessible — run: make up and wait 30 seconds"
fi

# ── .env file ──────────────────────────────────────────────────────────────
echo ""
echo "Configuration"

if [ -f ".env" ]; then
  ok ".env file exists"
  LLM_PROVIDER=$(grep "^LLM_PROVIDER=" .env 2>/dev/null | cut -d= -f2)
  ok "LLM_PROVIDER=${LLM_PROVIDER:-ollama}"
  if [ "${LLM_PROVIDER:-ollama}" = "anthropic" ]; then
    ANTHROPIC_KEY=$(grep "^ANTHROPIC_API_KEY=" .env 2>/dev/null | cut -d= -f2)
    if [ -z "$ANTHROPIC_KEY" ]; then
      fail "LLM_PROVIDER=anthropic but ANTHROPIC_API_KEY is empty in .env"
    else
      ok "ANTHROPIC_API_KEY is set"
    fi
  fi
else
  warn ".env not found — run: make setup (creates from .env.example)"
fi

# ── Summary ────────────────────────────────────────────────────────────────
echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ "$FAILED" -eq 0 ]; then
  echo -e "${GREEN}All prerequisites met. Start the app:${RESET}"
  echo ""
  echo "  Terminal 1:  make backend"
  echo "  Terminal 2:  make ai"
  echo "  Terminal 3:  make frontend"
  echo ""
  echo "  Then open:   http://localhost:5173"
else
  echo -e "${RED}Fix the issues above, then re-run this script.${RESET}"
fi
echo ""
