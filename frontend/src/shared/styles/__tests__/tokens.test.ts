import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// Read from disk rather than imported: `@tailwindcss/vite` claims .css files,
// and even under `?raw` what reaches the test is the processed output, in which
// the @theme block no longer exists. The token declarations are the source of
// truth here, so the source file is what must be parsed. Resolved from the
// Vitest root (vite.config.ts's own directory) because `import.meta.url` is not
// a file:// URL inside a transformed module.
const MAIN_CSS = resolve(process.cwd(), 'src/shared/styles/main.css')
const mainCss = readFileSync(MAIN_CSS, 'utf-8')

/**
 * C-1 of `docs/tasks/2026-08-21-visual-refinement-phase1-token-adoption`.
 *
 * The refactor replaces ~940 literal declarations with `var(--...)` references
 * on the claim that each token is exactly equal to the literal it stands for.
 * This table is that claim, written down. It is what makes the substitution
 * mechanically safe: if `--font-size-sm` is not exactly `0.875rem`, this fails
 * and no component needs checking.
 *
 * It also serves AC-8 in the other direction. Every row below is a value the
 * codebase now depends on in dozens of places, so a phase-2 edit that retunes
 * the ramp fails loudly here - once, with the old and new value side by side -
 * rather than silently everywhere.
 */

/** Extracts `@theme { ... }`'s declarations, brace-counted rather than regexed. */
function themeBlock(css: string): Record<string, string> {
  const start = css.indexOf('@theme {')
  if (start === -1) throw new Error('main.css has no @theme block')
  let depth = 0
  let end = -1
  for (let i = css.indexOf('{', start); i < css.length; i++) {
    if (css[i] === '{') depth++
    else if (css[i] === '}') {
      depth--
      if (depth === 0) {
        end = i
        break
      }
    }
  }
  if (end === -1) throw new Error('main.css @theme block is unterminated')

  const out: Record<string, string> = {}
  const re = /^\s*(--[a-z0-9-]+)\s*:\s*([^;]+);/gim
  let m: RegExpExecArray | null
  const body = css.slice(start, end)
  while ((m = re.exec(body)) !== null) out[m[1]] = m[2].trim()
  return out
}

const tokens = themeBlock(mainCss)

/**
 * Token -> its declared value and the literal notation it replaced in the
 * components. Both notations are listed where the codebase used both, because
 * a grep for one of them found only half its call sites before this dossier -
 * which is how STable's body text and SButton's md label came to be maintained
 * as unrelated numbers.
 */
const EXPECTED: [token: string, value: string, equals: string][] = [
  // Type ramp
  ['--font-size-2xs', '0.625rem', '10px'],
  ['--font-size-xs', '0.75rem', '12px'],
  ['--font-size-sm', '0.875rem', '14px'],
  ['--font-size-code', '0.8125rem', '13px'],
  ['--font-size-md', '1rem', '16px'],
  ['--font-size-lg', '1.125rem', '18px'],
  ['--font-size-xl', '1.25rem', '20px'],
  ['--font-size-2xl', '1.5rem', '24px'],
  ['--font-size-3xl', '1.875rem', '30px'],
  // Line height
  ['--line-none', '1', '1'],
  ['--line-tight', '1.3', '1.3'],
  ['--line-snug', '1.4', '1.4'],
  ['--line-normal', '1.5', '1.5'],
  ['--line-relaxed', '1.6', '1.6'],
  // Weight
  ['--weight-normal', '400', '400'],
  ['--weight-medium', '500', '500'],
  ['--weight-semibold', '600', '600'],
  ['--weight-bold', '700', '700'],
  // Spacing, including the three half-steps
  ['--space-0-5', '2px', '2px'],
  ['--space-1', '4px', '0.25rem'],
  ['--space-1-5', '6px', '0.375rem'],
  ['--space-2', '8px', '0.5rem'],
  ['--space-2-5', '10px', '10px'],
  ['--space-3', '12px', '0.75rem'],
  ['--space-4', '16px', '1rem'],
  ['--space-5', '20px', '1.25rem'],
  ['--space-6', '24px', '1.5rem'],
  ['--space-8', '32px', '2rem'],
  ['--space-10', '40px', '2.5rem'],
  ['--space-12', '48px', '3rem'],
  // Control heights
  ['--control-h-sm', '32px', '32px'],
  ['--control-h-md', '40px', '40px'],
  ['--control-h-lg', '48px', '48px'],
  // Semantic elevation
  ['--elevation-0', 'none', 'none'],
  ['--elevation-1', 'var(--shadow-sm)', 'var(--shadow-sm)'],
  ['--elevation-2', 'var(--shadow-md)', 'var(--shadow-md)'],
  ['--elevation-3', 'var(--shadow-lg)', 'var(--shadow-lg)'],
]

describe('design tokens', () => {
  it('parses a plausible @theme block', () => {
    // A regex that silently stops matching would make every assertion below
    // pass vacuously against an empty object.
    expect(Object.keys(tokens).length).toBeGreaterThan(60)
  })

  it.each(EXPECTED)('%s is %s (the literal %s it replaced)', (token, value) => {
    expect(tokens[token], `${token} is not declared in main.css's @theme block`).toBe(value)
  })

  it('declares every --space-*, --font-size-*, --line-* and --weight-* token in the table', () => {
    // The table is only a guarantee if it is complete: a token added to
    // main.css and left out here is a value nothing pins.
    const covered = new Set(EXPECTED.map(([t]) => t))
    const governed = Object.keys(tokens).filter((t) =>
      /^--(space|font-size|line|weight|control-h|elevation)-/.test(t),
    )
    expect(governed.filter((t) => !covered.has(t))).toEqual([])
  })

  it('never redefines a type or spacing token per theme', () => {
    // Colour and shadow switch with the theme; size does not. A dark-mode
    // override of a size token would make the parity baseline theme-dependent
    // and every substitution in this dossier conditionally wrong.
    const darkStart = mainCss.indexOf(':root[data-theme="dark"]')
    expect(darkStart).toBeGreaterThan(-1)
    const dark = mainCss.slice(darkStart, mainCss.indexOf('\n  }', darkStart))
    const offenders = [...dark.matchAll(/^\s*(--(?:space|font-size|line|weight|control-h)-[a-z0-9-]+)\s*:/gim)]
    expect(offenders.map((m) => m[1])).toEqual([])
  })
})
