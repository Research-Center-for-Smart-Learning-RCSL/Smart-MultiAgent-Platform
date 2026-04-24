#!/usr/bin/env bash
# CI gate #8 — Every view has >= 1 integration test (R24.39).
set -euo pipefail

FAILED=0
TOTAL=0
COVERED=0

while IFS= read -r -d '' view; do
  TOTAL=$((TOTAL + 1))
  basename=$(basename "$view" .vue)
  dir=$(dirname "$view")
  slice_dir=$(echo "$dir" | sed 's|/views$||')
  test_dir="$slice_dir/__tests__"

  # Look for a test file matching the view name
  if [ -d "$test_dir" ]; then
    if ls "$test_dir"/"$basename"*.test.ts "$test_dir"/"$basename"*.spec.ts 2>/dev/null | grep -q .; then
      COVERED=$((COVERED + 1))
      continue
    fi
  fi

  echo "FAIL: $view has no integration test in $test_dir/"
  FAILED=1
done < <(find src/slices -name '*View.vue' -print0 2>/dev/null; find src/app/views -name '*.vue' -print0 2>/dev/null)

echo ""
echo "Views: $TOTAL total, $COVERED covered."

if [ "$FAILED" -eq 1 ]; then
  echo "Gate #8 FAILED: Some views lack integration tests."
  exit 1
fi

echo "Gate #8 passed."
