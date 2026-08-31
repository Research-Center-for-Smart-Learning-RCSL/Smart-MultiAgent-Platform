#!/usr/bin/env bash
# CI gate #13b — prove the generated-ApiError restriction still fires, and only
# where it should.
#
# The rule it guards exists because the generated client's `ApiError` is
# unreachable: `transport/axios.ts` installs its problem+json handler on the
# bare axios singleton the generated services use, so `parseProblem` has
# already produced a `@shared/errors` instance before `core/request.ts` could
# throw its own. An `instanceof` against the generated class is dead code, and
# a test constructing that same class is green for the wrong reason — the exact
# pair of defects this gate was written after.
#
# `pnpm lint` cannot certify the rule on its own: once the offending imports are
# removed from the tree, a broken selector, a renamed rule and a working gate
# all produce identical silence. So assert on behaviour. Two negative probes
# must be reported, three positive ones must not — the positives matter as much,
# because a selector that rejects `@shared/errors.ApiError` or the generated
# DTOs would be "enforcing" by breaking the code it is steering people toward.
#
# The `*.test.ts` negative is the load-bearing one: the test override disables
# `no-restricted-imports` wholesale, so only a separate `no-restricted-syntax`
# selector survives there.
set -euo pipefail

RULE="no-restricted-syntax"
PROD_PROBE="src/slices/notifications/zz-apierror-probe.ts"
TEST_PROBE="src/slices/notifications/__tests__/zz-apierror-probe.test.ts"

cleanup() { rm -f "$PROD_PROBE" "$TEST_PROBE"; }
trap cleanup EXIT

run_probe() {
  # eslint exits non-zero on lint errors; capture output without tripping set -e.
  npx eslint "$1" 2>&1 || true
}

expect_rejected() {
  local probe="$1" what="$2" out
  out="$(run_probe "$probe")"
  if ! printf '%s' "$out" | grep -q "$RULE"; then
    echo "Gate #13b FAILED: $what was not reported."
    echo "The generated ApiError is unreachable and must never be named."
    echo "Check the selector shapes in eslint.config.js (gate #13)."
    echo ""
    echo "$out"
    exit 1
  fi
}

expect_accepted() {
  local probe="$1" what="$2" out
  out="$(run_probe "$probe")"
  if printf '%s' "$out" | grep -q "$RULE"; then
    echo "Gate #13b FAILED: $what was reported."
    echo "The rule must restrict the generated ApiError only — over-firing on"
    echo "the shared class or on generated DTOs makes it a rule people disable."
    echo ""
    echo "$out"
    exit 1
  fi
}

# --- negative: production named import ---
cat > "$PROD_PROBE" <<'EOF'
import { ApiError } from '@shared/api-client'
export const probe = (e: unknown): boolean => e instanceof ApiError
EOF
expect_rejected "$PROD_PROBE" "a production named import of the generated ApiError"

# --- negative: test-file named import (the override turns off the imports rule) ---
cat > "$TEST_PROBE" <<'EOF'
import { ApiError } from '@shared/api-client'
export const probe = (e: unknown): boolean => e instanceof ApiError
EOF
expect_rejected "$TEST_PROBE" "a *.test.ts named import of the generated ApiError"
rm -f "$TEST_PROBE"

# --- negative: re-export ---
cat > "$PROD_PROBE" <<'EOF'
export { ApiError } from '@shared/api-client'
EOF
expect_rejected "$PROD_PROBE" "a re-export of the generated ApiError"

# --- positive: the shared class the transport actually throws ---
cat > "$PROD_PROBE" <<'EOF'
import { ApiError } from '@shared/errors'
export const probe = (e: unknown): boolean => e instanceof ApiError
EOF
expect_accepted "$PROD_PROBE" "importing ApiError from @shared/errors"

# --- positive: other generated symbols ---
cat > "$PROD_PROBE" <<'EOF'
import { OpenAPI } from '@shared/api-client'
export const probe = OpenAPI.WITH_CREDENTIALS
EOF
expect_accepted "$PROD_PROBE" "importing a non-ApiError symbol from the generated client"

# --- positive: a same-named import from anywhere else ---
cat > "$PROD_PROBE" <<'EOF'
export { ApiError } from '@shared/errors'
EOF
expect_accepted "$PROD_PROBE" "re-exporting the shared ApiError"

echo "Gate #13b passed: $RULE rejects the generated ApiError in production, tests and re-exports, and permits the shared class and other generated symbols."
