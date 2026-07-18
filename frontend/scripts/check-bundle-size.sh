#!/usr/bin/env bash
# CI gate #9 — Bundle budget enforcement (R24.48).
# Initial bundle ≤ 250 KB gzip; per-view lazy chunk ≤ 200 KB gzip.
set -euo pipefail

DIST="dist/assets"
INITIAL_LIMIT=256000   # 250 KB
LAZY_LIMIT=204800      # 200 KB
FAILED=0

# Heavy 3rd-party libraries that are intentionally lazy-loaded inside the
# markdown renderer (loaded only when rendering chat messages). They cannot
# fit the per-view lazy budget without a major refactor; budget enforcement
# is intentionally relaxed for these named chunks.
EXEMPT_PREFIXES='^(mermaid|hljs)-'

if [ ! -d "$DIST" ]; then
  echo "ERROR: $DIST not found. Run 'npm run build' first."
  exit 1
fi

# AC-6 (2026-07-16-code-editor-syntax-highlighting): the CodeMirror JSON build
# (shared/ui/codeMirrorJson.ts — core + lang-json + lint, one dynamic import,
# never a static import alongside a second @codemirror/lang-* grammar per
# AC-7) is pinned well under LAZY_LIMIT, not merely under it. Measured
# 150 911 B gz on 2026-07-17 via a throwaway vite@6 project (see the task
# spec §5); the integrated build dedupes shared deps into vendor chunks and
# lands lower. 180 000 B is a defended margin, not the observed number — if
# a future grammar addition pushes past it, that is the signal to split
# further before LAZY_LIMIT itself is at risk.
CODEMIRROR_JSON_LIMIT=180000
CODEMIRROR_JSON_FOUND=0

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
  elif echo "$basename" | grep -qE "$EXEMPT_PREFIXES"; then
    echo "SKIP: $basename ($size bytes gzip) — exempt heavy lazy lib"
  elif echo "$basename" | grep -qE '^codeMirrorJson-'; then
    CODEMIRROR_JSON_FOUND=1
    if [ "$size" -gt "$CODEMIRROR_JSON_LIMIT" ]; then
      echo "FAIL: $basename ($size bytes gzip) exceeds the CodeMirror JSON budget ($CODEMIRROR_JSON_LIMIT bytes)"
      FAILED=1
    else
      echo "OK:   $basename ($size bytes gzip) within CodeMirror JSON budget ($CODEMIRROR_JSON_LIMIT bytes)"
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

# A silent zero-match here (chunk renamed, merged into another bundle, etc.)
# would mean this budget stops being enforced without CI ever going red —
# exactly the dead-exemption failure mode §5/FU-1 of the task spec found in
# the pre-existing mermaid/hljs exempt list. Fail loudly instead: the chunk
# is produced by a static import (shared/ui/SCodeEditor.vue), so it must
# exist in every build.
if [ "$CODEMIRROR_JSON_FOUND" -eq 0 ]; then
  echo "FAIL: no codeMirrorJson-*.js chunk found in $DIST — the CodeMirror JSON budget was not checked"
  FAILED=1
fi

if [ "$FAILED" -eq 1 ]; then
  echo ""
  echo "Bundle budget check FAILED."
  exit 1
fi

echo ""
echo "Bundle budget check passed."
