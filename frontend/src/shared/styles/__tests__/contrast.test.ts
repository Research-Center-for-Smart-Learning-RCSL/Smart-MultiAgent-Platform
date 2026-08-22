import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * AC-9 of `docs/tasks/2026-08-21-visual-refinement-phase2-identity-and-depth`.
 *
 * `docs/UI/01-design-system.md` has required WCAG 2.1 AA of every component
 * since it was written, and nothing has ever measured it. Phase 2 moves every
 * neutral onto a new axis and introduces a third surface role, which is exactly
 * the edit that can drop a foreground below its threshold without any visible
 * symptom - so the budget becomes a test rather than a one-off measurement, and
 * stays enforced the next time a colour moves.
 *
 * Read from disk for the reason tokens.test.ts gives: `@tailwindcss/vite`
 * claims .css files, so what an import would yield is processed output in which
 * the `@theme` block no longer exists.
 */
const MAIN_CSS = resolve(process.cwd(), 'src/shared/styles/main.css')
const mainCss = readFileSync(MAIN_CSS, 'utf-8')

/** Declarations of one brace-delimited block, by the text that opens it. */
function block(css: string, opener: string): Record<string, string> {
  const start = css.indexOf(opener)
  if (start === -1) throw new Error(`main.css has no ${opener} block`)
  let depth = 0
  let end = -1
  for (let i = css.indexOf('{', start); i < css.length; i++) {
    if (css[i] === '{') depth++
    else if (css[i] === '}' && --depth === 0) {
      end = i
      break
    }
  }
  if (end === -1) throw new Error(`main.css ${opener} block is unterminated`)

  const out: Record<string, string> = {}
  const re = /^\s*(--[a-z0-9-]+)\s*:\s*(#[0-9a-f]{3,8})\s*;/gim
  let m: RegExpExecArray | null
  const body = css.slice(start, end)
  while ((m = re.exec(body)) !== null) out[m[1]] = m[2].toLowerCase()
  return out
}

const light = block(mainCss, '@theme {')
// Dark declares only what it overrides; everything else is inherited from light.
const dark = { ...light, ...block(mainCss, ':root[data-theme="dark"] {') }

const THEMES: [name: string, tokens: Record<string, string>][] = [
  ['light', light],
  ['dark', dark],
]

/** sRGB relative luminance, WCAG 2.1 §relative-luminance. */
function luminance(hex: string): number {
  const h = hex.slice(1)
  const full = h.length === 3 ? [...h].map((c) => c + c).join('') : h
  const channel = (i: number): number => {
    const c = Number.parseInt(full.slice(i * 2, i * 2 + 2), 16) / 255
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4
  }
  return 0.2126 * channel(0) + 0.7152 * channel(1) + 0.0722 * channel(2)
}

function contrast(fg: string, bg: string): number {
  const a = luminance(fg)
  const b = luminance(bg)
  const [hi, lo] = a > b ? [a, b] : [b, a]
  return (hi + 0.05) / (lo + 0.05)
}

/** CIE L*, used for the canvas-to-sheet separation rather than for contrast. */
function lightness(hex: string): number {
  const y = luminance(hex)
  return y <= 216 / 24389 ? y * (24389 / 27) : 116 * Math.cbrt(y) - 16
}

/**
 * Every foreground-on-background pair AC-9 names.
 *
 * 4.5:1 is AA for body text. The status `*-on`/`*-tint` pairs are held to the
 * same bar because they carry sentences, not glyphs - an SAlert's body renders
 * in `--color-*-on` on `--color-*-tint`.
 */
const TEXT_PAIRS: [fg: string, bg: string][] = [
  ['--color-fg', '--color-canvas'],
  ['--color-fg', '--color-bg'],
  ['--color-fg', '--color-surface'],
  ['--color-muted', '--color-canvas'],
  ['--color-muted', '--color-bg'],
  ['--color-muted', '--color-surface'],
  ['--color-sidebar-text', '--color-sidebar-bg'],
  ['--color-sidebar-section-text', '--color-sidebar-bg'],
  // The sidebar's interaction states. Not in AC-9's list, added because phase 2
  // moved every one of them: the label colours changed with the axis, and
  // --color-accent-tint-hover went from zero consumers to being the fill under
  // an active row's hover and press.
  ['--color-sidebar-text', '--color-sidebar-hover'],
  ['--color-sidebar-active-text', '--color-sidebar-active-bg'],
  ['--color-sidebar-active-text', '--color-accent-tint-hover'],
  ['--color-accent', '--color-bg'],
  // Content ON a filled surface, not beside it. These were a hard-coded #fff
  // in nine components, which measured 2.54:1 against the dark theme's accent
  // and 2.77:1 against its danger - the primary button in the product, below
  // AA in one of the two themes, with nothing measuring it.
  ['--color-on-accent', '--color-accent'],
  ['--color-on-danger', '--color-danger'],
  ['--color-neutral-on', '--color-neutral-tint'],
  ['--color-info-on', '--color-info-tint'],
  ['--color-success-on', '--color-success-tint'],
  ['--color-warning-on', '--color-warning-tint'],
  ['--color-danger-on', '--color-danger-tint'],
]

describe('WCAG contrast budget', () => {
  it('parses both theme blocks', () => {
    // Without this every assertion below passes vacuously the day the regex
    // stops matching - the failure mode a source-parsing test is most prone to.
    expect(Object.keys(light).length).toBeGreaterThan(25)
    expect(Object.keys(dark).length).toBeGreaterThan(25)
    expect(dark['--color-bg']).not.toBe(light['--color-bg'])
  })

  describe.each(THEMES)('%s theme', (themeName, tokens) => {
    it.each(TEXT_PAIRS)('%s on %s meets 4.5:1', (fg, bg) => {
      expect(tokens[fg], `${fg} is not declared for the ${themeName} theme`).toBeDefined()
      expect(tokens[bg], `${bg} is not declared for the ${themeName} theme`).toBeDefined()
      const ratio = contrast(tokens[fg], tokens[bg])
      expect(
        ratio,
        `${fg} (${tokens[fg]}) on ${bg} (${tokens[bg]}) is ${ratio.toFixed(2)}:1`,
      ).toBeGreaterThanOrEqual(4.5)
    })

    /**
     * The reason a card reads as raised. Below 3 points the sheet disappears
     * into the canvas and the elevation ladder has nothing to sit on; above 5
     * the shell reads as banded. Both themes are held to the same window,
     * which is what makes one set of --elevation-* tokens correct in both -
     * the premise those tokens were written on and which was never true.
     */
    it('separates the canvas from a raised sheet by 3 to 5 points of L*', () => {
      const gap = Math.abs(lightness(tokens['--color-bg']) - lightness(tokens['--color-canvas']))
      expect(gap, `canvas-to-sheet gap is ${gap.toFixed(2)} L*`).toBeGreaterThanOrEqual(3)
      expect(gap, `canvas-to-sheet gap is ${gap.toFixed(2)} L*`).toBeLessThanOrEqual(5)
    })

    /**
     * WCAG 2.1 1.4.11. It applies to a form control's boundary - the outline is
     * the only thing that identifies a text field as present - and not to a
     * container's, which is identified by its content and its elevation. That
     * is the distinction --color-border-strong exists to make; before it, one
     * token drew both and neither could satisfy its own requirement without
     * ruining the other's.
     */
    it('draws a form control boundary at 3:1', () => {
      const ratio = contrast(tokens['--color-border-strong'], tokens['--color-bg'])
      expect(ratio, `--color-border-strong is ${ratio.toFixed(2)}:1`).toBeGreaterThanOrEqual(3)
    })

    /**
     * The three rule weights must be ordered and must actually differ. This is
     * the whole content of the split: a single token doing every job is what
     * made a card's edge and a table row rule the same line.
     */
    it('orders control boundary over container boundary over interior rule', () => {
      const control = contrast(tokens['--color-border-strong'], tokens['--color-bg'])
      const boundary = contrast(tokens['--color-border'], tokens['--color-bg'])
      const interior = contrast(tokens['--color-border-subtle'], tokens['--color-bg'])
      expect(control).toBeGreaterThan(boundary)
      expect(boundary).toBeGreaterThan(interior)
      // Visible at all: a rule nobody can see is not a lighter rule.
      expect(interior).toBeGreaterThanOrEqual(1.1)
    })

    /**
     * An interior rule is drawn ON a recessed fill as often as on a sheet - a
     * table header, a card footer, an editor's line-number gutter all set
     * `background: var(--color-surface)` and then a --color-border-subtle rule.
     * Measuring the border weights against --color-bg alone let the dark theme
     * ship with --color-border-subtle byte-identical to --color-surface, which
     * is not a lighter rule but no rule.
     */
    it('keeps an interior rule visible on a recessed fill', () => {
      const onSurface = contrast(tokens['--color-border-subtle'], tokens['--color-surface'])
      expect(
        onSurface,
        `--color-border-subtle on --color-surface is ${onSurface.toFixed(2)}:1`,
      ).toBeGreaterThanOrEqual(1.1)
    })

    /**
     * A tint is a fill, and a fill that matches what it sits on is not a fill.
     * `neutral` is SBadge's default variant, so this one reaches every surface
     * role in the product; it went unguarded until --color-neutral-tint and
     * --color-canvas were assigned the same value.
     */
    it('gives a tint fill a value distinct from every surface it can sit on', () => {
      for (const surface of ['--color-canvas', '--color-bg', '--color-surface']) {
        const ratio = contrast(tokens['--color-neutral-tint'], tokens[surface])
        expect(
          ratio,
          `--color-neutral-tint on ${surface} is ${ratio.toFixed(2)}:1`,
        ).toBeGreaterThanOrEqual(1.1)
      }
    })
  })

  /**
   * The measured table, emitted for §15 of the dossier rather than asserted.
   * AC-9 requires the ratios to be listed, not merely to pass.
   */
  it('reports every measured pair', () => {
    const rows: string[] = []
    for (const [themeName, tokens] of THEMES) {
      for (const [fg, bg] of TEXT_PAIRS) {
        rows.push(`${themeName} ${fg}/${bg} ${contrast(tokens[fg], tokens[bg]).toFixed(2)}`)
      }
      for (const b of ['--color-border-strong', '--color-border', '--color-border-subtle']) {
        rows.push(`${themeName} ${b}/--color-bg ${contrast(tokens[b], tokens['--color-bg']).toFixed(2)}`)
      }
      const gap = Math.abs(lightness(tokens['--color-bg']) - lightness(tokens['--color-canvas']))
      rows.push(`${themeName} canvas-to-sheet L* gap ${gap.toFixed(2)}`)
    }
    expect(rows.length).toBe(THEMES.length * (TEXT_PAIRS.length + 4))
    console.info(rows.join('\n'))
  })
})
