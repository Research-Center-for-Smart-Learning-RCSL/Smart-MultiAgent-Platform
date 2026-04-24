#!/usr/bin/env bash
set -euo pipefail

# CI gate: regenerate OpenAPI types and fail if committed output differs.
# Usage: bash scripts/check-openapi-drift.sh

OUTDIR="src/shared/api-client"

echo "Regenerating OpenAPI types…"
npm run gen:api --silent

if git diff --quiet -- "$OUTDIR"; then
  echo "✅ OpenAPI types are in sync."
  exit 0
else
  echo "❌ OpenAPI types are stale. Run 'npm run gen:api' and commit."
  git diff --stat -- "$OUTDIR"
  exit 1
fi
