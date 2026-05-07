#!/usr/bin/env bash
# Idempotently set KEY=VALUE in .env (creates if missing, updates if present).
# Usage: ./scripts/upsert-env.sh KEY VALUE
set -euo pipefail
KEY="${1:?key required}"
VAL="${2:-}"
ENV_FILE=".env"
test -f "$ENV_FILE" || touch "$ENV_FILE"

if grep -qE "^${KEY}=" "$ENV_FILE"; then
  # macOS sed needs '' after -i; gnu sed doesn't. Use a tmp file for portability.
  awk -v k="$KEY" -v v="$VAL" 'BEGIN{FS=OFS="="} $1==k{$2=v;found=1;print;next}{print} END{if(!found)print k"="v}' \
      "$ENV_FILE" > "${ENV_FILE}.tmp"
  mv "${ENV_FILE}.tmp" "$ENV_FILE"
else
  echo "${KEY}=${VAL}" >> "$ENV_FILE"
fi
