#!/usr/bin/env bash
# CI gate #10 — Type coverage >= 95% with no explicit `any` (R24.48).
set -euo pipefail

THRESHOLD=95

echo "Running type-coverage (threshold: ${THRESHOLD}%)..."

OUTPUT=$(npx type-coverage --project tsconfig.app.json --at-least "$THRESHOLD" --strict --detail 2>&1) || {
  echo "$OUTPUT"
  echo ""
  echo "Gate #10 FAILED: Type coverage below ${THRESHOLD}%."
  exit 1
}

echo "$OUTPUT"
echo "Gate #10 passed."
