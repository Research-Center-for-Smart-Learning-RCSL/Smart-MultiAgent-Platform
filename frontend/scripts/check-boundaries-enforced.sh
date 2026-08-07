#!/usr/bin/env bash
# CI gate #1b — prove boundaries/dependencies still enforces SLICE_DEPS.
#
# Gate #1 fails open. eslint-plugin-boundaries reports an unusable rule
# configuration as a warning on stderr and then evaluates nothing, so a broken
# selector shape, a renamed rule, a dropped `import/resolver` entry or a plugin
# major bump all leave `pnpm lint` green while cross-slice imports stop being
# checked. `pnpm lint` cannot detect that: a gate that inspects nothing and a
# gate that finds no violations produce identical output.
#
# So assert on behaviour instead of on config. Write a known-forbidden import
# and require an error, then write a permitted one and require silence. The
# second half matters as much as the first: a rule that rejects everything is
# just as broken, and would otherwise look like a working gate.
set -euo pipefail

# notifications may depend on identity only (SLICE_DEPS in eslint.config.js).
# Both probes import through the slice's index.ts, so gate #2's
# no-restricted-imports patterns (which only cover deep paths) cannot be what
# produces the error — this isolates gate #1.
PROBE="src/slices/notifications/zz-boundaries-probe.ts"
RULE="boundaries/dependencies"

cleanup() { rm -f "$PROBE"; }
trap cleanup EXIT

run_probe() {
  # eslint exits non-zero on lint errors; capture output without tripping set -e.
  npx eslint "$PROBE" 2>&1 || true
}

printf "import { workflowRoutes } from '@slices/workflow'\nexport const probe = workflowRoutes\n" > "$PROBE"
forbidden_output="$(run_probe)"
if ! printf '%s' "$forbidden_output" | grep -q "$RULE"; then
  echo "Gate #1b FAILED: a forbidden cross-slice import was not reported."
  echo "notifications -> workflow is not in SLICE_DEPS and must raise $RULE."
  echo "Gate #1 is not enforcing. Check the rule name, the selector shapes in"
  echo "eslint.config.js, and the 'import/resolver' setting."
  echo ""
  echo "$forbidden_output"
  exit 1
fi

printf "import { identityKeys } from '@slices/identity'\nexport const probe = identityKeys\n" > "$PROBE"
allowed_output="$(run_probe)"
if printf '%s' "$allowed_output" | grep -q "$RULE"; then
  echo "Gate #1b FAILED: a permitted cross-slice import was reported."
  echo "notifications -> identity is declared in SLICE_DEPS and must be allowed."
  echo "Gate #1 is rejecting everything, which is not enforcement either."
  echo ""
  echo "$allowed_output"
  exit 1
fi

echo "Gate #1b passed: $RULE rejects a forbidden slice dependency and permits a declared one."
