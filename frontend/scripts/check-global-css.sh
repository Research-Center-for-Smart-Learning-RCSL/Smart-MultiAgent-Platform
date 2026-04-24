#!/usr/bin/env bash
# CI gate #6 — No global (non-scoped) <style> outside shared/styles/ (R24.30).
set -euo pipefail

FAILED=0
CHECKED=0

while IFS= read -r -d '' file; do
  CHECKED=$((CHECKED + 1))
  # Extract <style> tags that lack "scoped" or "module" attribute
  if grep -Pn '<style(?![^>]*(scoped|module))[^>]*>' "$file" | grep -v '^\s*$' > /dev/null 2>&1; then
    echo "FAIL: $file has non-scoped <style> block (gate #6)"
    FAILED=1
  fi
done < <(find src/slices src/app -name '*.vue' -print0 2>/dev/null)

if [ "$CHECKED" -eq 0 ]; then
  echo "WARN: No .vue files found to check."
  exit 0
fi

if [ "$FAILED" -eq 1 ]; then
  echo ""
  echo "Gate #6 FAILED: Non-scoped <style> blocks found outside shared/styles/."
  echo "Use <style scoped> in component files. Global styles belong in shared/styles/ only."
  exit 1
fi

echo "Gate #6 passed: All $CHECKED component <style> blocks are scoped."
