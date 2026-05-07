#!/bin/sh
# Downloads LLM models into Ollama.
# Run via: docker compose -f docker-compose.infra.yml --profile pull-models up model-puller
#
# Choose models based on your hardware:
#
#  RAM   Recommended config
#  8 GB  llama3.1:8b only
#  16 GB llama3.1:8b + deepseek-coder-v2:16b
#  32 GB all three models below
#  64 GB+ swap deepseek-r1:14b for deepseek-r1:32b for higher quality synthesis

set -e

OLLAMA="${OLLAMA_HOST:-http://ollama:11434}"

pull_model() {
  MODEL=$1
  echo ""
  echo "━━━ Pulling $MODEL ━━━"
  curl -sf -X POST "$OLLAMA/api/pull" \
    -H "Content-Type: application/json" \
    -d "{\"name\": \"$MODEL\", \"stream\": false}" \
    | grep -E '"status"|"error"' || true
  echo "✓ $MODEL ready"
}

echo "Waiting for Ollama to be ready..."
until curl -sf "$OLLAMA/api/tags" > /dev/null; do sleep 2; done
echo "Ollama is up."

# ── Fast structured extraction (entity pass, relationship pass) ──────────────
# 4.7 GB download · works on 8 GB RAM · CPU or GPU
pull_model "llama3.1:8b"

# ── Code-aware extraction (entity pass for code files) ───────────────────────
# 9 GB download · needs 16 GB RAM
pull_model "deepseek-coder-v2:16b"

# ── Reasoning + synthesis (context synthesis, gap detection, query interface) ─
# 8 GB download · needs 16 GB RAM · best quality available locally
pull_model "deepseek-r1:14b"

# ── Uncomment for even higher quality on 32 GB+ machines ─────────────────────
# pull_model "deepseek-r1:32b"
# pull_model "llama3.1:70b"

echo ""
echo "All models downloaded. Listing available models:"
curl -s "$OLLAMA/api/tags" | grep -o '"name":"[^"]*"'
