#!/usr/bin/env bash
# CI gate — `typecheck` must actually compile src/**, not silently no-op
# (2026-07-03 frontend-typecheck-gate task, FU-1 from the conversation audit).
#
# vue-tsc --noEmit against a solution-style root tsconfig.json ("files": [])
# checks zero files and exits 0. This gate proves the `typecheck` script is
# really compiling source by injecting a deliberately broken fixture and
# asserting the reported error count increases — regardless of how many
# pre-existing errors the tree already has. If the gate ever regresses to a
# no-op (e.g. someone drops --build from the script), this delta is always
# zero and the check fails.
set -euo pipefail

SMOKE_FILE="src/__typecheck_gate_smoke__.ts"

cleanup() {
  rm -f "$SMOKE_FILE"
}
trap cleanup EXIT

if [ -e "$SMOKE_FILE" ]; then
  echo "FAIL: $SMOKE_FILE already exists — refusing to overwrite."
  exit 1
fi

echo "Running baseline typecheck..."
BASELINE_OUTPUT=$(pnpm run typecheck 2>&1 || true)
BASELINE=$(echo "$BASELINE_OUTPUT" | grep -c 'error TS' || true)

cat > "$SMOKE_FILE" <<'EOF'
// Deliberately invalid — proves the typecheck gate actually compiles this file.
export const __typecheck_gate_smoke__: number = 'not-a-number'
EOF

echo "Running typecheck with a deliberately broken fixture..."
WITH_SMOKE_OUTPUT=$(pnpm run typecheck 2>&1 || true)
WITH_SMOKE=$(echo "$WITH_SMOKE_OUTPUT" | grep -c 'error TS' || true)

rm -f "$SMOKE_FILE"

if [ "$WITH_SMOKE" -le "$BASELINE" ]; then
  echo "$WITH_SMOKE_OUTPUT"
  echo ""
  echo "FAIL: injecting a deliberate type error did not increase the typecheck error count"
  echo "(baseline=$BASELINE, with smoke fixture=$WITH_SMOKE)."
  echo "The typecheck gate is not compiling src/** — it has regressed to a no-op."
  exit 1
fi

if ! echo "$WITH_SMOKE_OUTPUT" | grep -q "$SMOKE_FILE"; then
  echo "$WITH_SMOKE_OUTPUT"
  echo ""
  echo "FAIL: typecheck output did not report an error in $SMOKE_FILE."
  exit 1
fi

echo "Typecheck gate is live: baseline=$BASELINE error(s), with smoke fixture=$WITH_SMOKE error(s)."
echo "Gate check passed."
