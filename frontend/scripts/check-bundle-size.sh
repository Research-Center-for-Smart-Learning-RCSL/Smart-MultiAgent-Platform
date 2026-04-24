#!/usr/bin/env bash
# CI gate #9 — Bundle budget enforcement (R24.48).
# Initial bundle ≤ 250 KB gzip; per-view lazy chunk ≤ 200 KB gzip.
set -euo pipefail

DIST="dist/assets"
INITIAL_LIMIT=256000   # 250 KB
LAZY_LIMIT=204800      # 200 KB
FAILED=0

if [ ! -d "$DIST" ]; then
  echo "ERROR: $DIST not found. Run 'npm run build' first."
  exit 1
fi

for file in "$DIST"/*.js; do
  size=$(gzip -c "$file" | wc -c)
  basename=$(basename "$file")

  # Initial chunks: index-*.js and vendor-*.js
  if echo "$basename" | grep -qE '^(index|vendor)-'; then
    if [ "$size" -gt "$INITIAL_LIMIT" ]; then
      echo "FAIL: $basename ($size bytes gzip) exceeds initial budget ($INITIAL_LIMIT bytes)"
      FAILED=1
    else
      echo "OK:   $basename ($size bytes gzip)"
    fi
  else
    if [ "$size" -gt "$LAZY_LIMIT" ]; then
      echo "FAIL: $basename ($size bytes gzip) exceeds lazy budget ($LAZY_LIMIT bytes)"
      FAILED=1
    else
      echo "OK:   $basename ($size bytes gzip)"
    fi
  fi
done

if [ "$FAILED" -eq 1 ]; then
  echo ""
  echo "Bundle budget check FAILED."
  exit 1
fi

echo ""
echo "Bundle budget check passed."
