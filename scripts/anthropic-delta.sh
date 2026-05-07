#!/usr/bin/env bash
# Print BEFORE-AFTER delta with 4 decimals.
set -euo pipefail
B="${1:-0}"; A="${2:-0}"
python3 -c "print(f'{float('$A') - float('$B'):.4f}')" 2>/dev/null \
  || awk "BEGIN{printf \"%.4f\", $A - $B}"
