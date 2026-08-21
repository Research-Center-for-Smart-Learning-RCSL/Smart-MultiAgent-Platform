import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// Scoped `<style>` blocks are compiled away by @vitejs/plugin-vue and never
// injected into jsdom (vitest.config.ts sets no `css: true`), and jsdom would
// perform no layout on them anyway. So a rule that must exist for a layout
// outcome to hold is pinned by reading the component source instead. These are
// structural guards: they prove the declaration is present, never that it
// renders correctly. The visual half belongs to Playwright or a manual pass.

// Anchored on the working directory rather than import.meta.url: vitest's
// module runner does not guarantee a file: URL there, and both `pnpm test`
// (run from frontend/) and a repository-root invocation must resolve.
function srcPath(srcRelativePath: string): string {
  for (const base of ['src', 'frontend/src']) {
    const candidate = resolve(process.cwd(), base, srcRelativePath)
    if (existsSync(candidate)) return candidate
  }
  throw new Error(`Cannot locate src/${srcRelativePath} from ${process.cwd()}`)
}

/** Concatenated contents of every `<style>` block in an SFC under `src/`. */
export function readComponentStyles(srcRelativePath: string): string {
  const file = readFileSync(srcPath(srcRelativePath), 'utf8')
  const blocks = [...file.matchAll(/<style\b[^>]*>([\s\S]*?)<\/style>/g)]
  if (blocks.length === 0) {
    throw new Error(`No <style> block found in ${srcRelativePath}`)
  }
  return blocks.map((m) => m[1]).join('\n')
}

/** Whole source text of an SFC under `src/`, for template-level assertions. */
export function readComponentSource(srcRelativePath: string): string {
  return readFileSync(srcPath(srcRelativePath), 'utf8')
}

/**
 * Body of the top-level rule whose selector list names `selector`, or null.
 *
 * Rules nested inside an at-rule (`@media`, `@supports`) are skipped, so a
 * breakpoint override can never be mistaken for the base rule it overrides —
 * which matters for SModal, where `.s-modal` is declared in both places.
 */
export function topLevelRule(css: string, selector: string): string | null {
  const src = css.replace(/\/\*[\s\S]*?\*\//g, '')
  let i = 0
  while (i < src.length) {
    const open = src.indexOf('{', i)
    if (open === -1) return null
    const close = matchBrace(src, open)
    if (close === -1) return null
    const prelude = src.slice(i, open).trim()
    if (!prelude.startsWith('@')) {
      const selectors = prelude.split(',').map((s) => s.trim())
      if (selectors.includes(selector)) return src.slice(open + 1, close)
    }
    i = close + 1
  }
  return null
}

/**
 * Body of the first top-level at-rule whose prelude contains `match`, or null.
 * Feed the result back to `topLevelRule` to reach a breakpoint override.
 */
export function atRuleBody(css: string, match: string): string | null {
  const src = css.replace(/\/\*[\s\S]*?\*\//g, '')
  let i = 0
  while (i < src.length) {
    const open = src.indexOf('{', i)
    if (open === -1) return null
    const close = matchBrace(src, open)
    if (close === -1) return null
    const prelude = src.slice(i, open).trim()
    if (prelude.startsWith('@') && prelude.includes(match)) {
      return src.slice(open + 1, close)
    }
    i = close + 1
  }
  return null
}

/** Value of `property` within a rule body, or null when it is not declared. */
export function declaration(ruleBody: string, property: string): string | null {
  const match = new RegExp(`(?:^|;)\\s*${property}\\s*:\\s*([^;]+)`, 'i').exec(ruleBody)
  return match?.[1]?.trim() ?? null
}

function matchBrace(src: string, open: number): number {
  let depth = 0
  for (let i = open; i < src.length; i += 1) {
    if (src[i] === '{') depth += 1
    else if (src[i] === '}') {
      depth -= 1
      if (depth === 0) return i
    }
  }
  return -1
}
