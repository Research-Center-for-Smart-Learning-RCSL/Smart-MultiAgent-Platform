#!/usr/bin/env bash
set -euo pipefail

# CI gate: regenerate the OpenAPI spec from the backend, regenerate the
# frontend TS client, and fail if either output drifts from what was
# committed.
# Usage: bash scripts/check-openapi-drift.sh

OUTDIR="src/shared/api-client"
BACKEND_DIR="$(cd "$(dirname "$0")/../../backend" && pwd)"
SPEC_PATH="$BACKEND_DIR/openapi.json"

echo "Exporting OpenAPI spec from backend…"
( cd "$BACKEND_DIR" && python -m scripts.export_openapi > openapi.json )

echo "Regenerating OpenAPI types…"
npm run gen:api --silent

DRIFTED=0
# Detect both tracked-modified AND untracked-new files under the codegen tree.
status_outdir=$(git status --porcelain -- "$OUTDIR")
if [ -n "$status_outdir" ]; then
  echo "❌ OpenAPI types are stale. Run 'make openapi-types' and commit."
  echo "$status_outdir"
  DRIFTED=1
fi

status_spec=$(git status --porcelain -- "$SPEC_PATH")
if [ -n "$status_spec" ]; then
  echo "❌ backend/openapi.json is stale. Run 'make openapi-types' and commit."
  echo "$status_spec"
  DRIFTED=1
fi

if [ "$DRIFTED" -eq 1 ]; then
  exit 1
fi

echo "✅ OpenAPI spec and types are in sync."
