import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

/**
 * Phase 1's FU-5, closed here.
 *
 * An unresolvable `var(--token)` does not fail, warn, or render red - it falls
 * back to the property's initial value in complete silence, which for a colour
 * means transparent or inherited. Phase 1 ran this as a one-off mechanical
 * check and found five custom properties that were referenced and never
 * declared anywhere in the codebase. Running it once found them; running it
 * every build is what stops a sixth.
 *
 * Declarations are collected from the whole tree, not just main.css, because a
 * component may legitimately declare its own (SCard's --card-pad, AppShell's
 * --topbar-height-total).
 */
const MAIN_CSS = resolve(process.cwd(), 'src/shared/styles/main.css')
const mainCss = readFileSync(MAIN_CSS, 'utf-8')

const sources = import.meta.glob('/src/**/*.vue', { eager: true, query: '?raw', import: 'default' }) as Record<
  string,
  string
>

/** `--name:` in a stylesheet. */
const DECLARATION = /(--[a-z][a-z0-9-]*)\s*:/gi
/**
 * `'--name':` in a style object, which is how the animated components hand a
 * per-element value to CSS (LandingIntro's node offsets, ChatroomView's rail
 * width and keyboard inset). These never appear in a `<style>` block, so
 * without this the sweep would report each of them as unresolvable.
 */
const BOUND_DECLARATION = /['"](--[a-z][a-z0-9-]*)['"]\s*:/gi
const REFERENCE = /var\(\s*(--[a-z][a-z0-9-]*)/gi

/**
 * Custom properties the browser or a dependency supplies, which therefore have
 * no declaration in our source.
 */
const EXTERNAL = new Set<string>()

function declared(): Set<string> {
  const out = new Set<string>()
  for (const text of [mainCss, ...Object.values(sources)]) {
    for (const m of text.matchAll(DECLARATION)) out.add(m[1])
    for (const m of text.matchAll(BOUND_DECLARATION)) out.add(m[1])
  }
  return out
}

describe('custom property resolvability', () => {
  const known = declared()

  it('collected a plausible vocabulary', () => {
    expect(known.size).toBeGreaterThan(60)
    expect(known.has('--color-bg')).toBe(true)
  })

  it('resolves every var() reference in the tree', () => {
    const offenders: string[] = []
    for (const [path, text] of Object.entries(sources)) {
      if (path.includes('__tests__/')) continue
      for (const m of text.matchAll(REFERENCE)) {
        if (known.has(m[1]) || EXTERNAL.has(m[1])) continue
        offenders.push(`${path.replace('/src/', '')}: var(${m[1]})`)
      }
    }
    expect(offenders).toEqual([])
  })

  it('resolves every var() reference inside main.css itself', () => {
    const offenders = [...mainCss.matchAll(REFERENCE)]
      .map((m) => m[1])
      .filter((name) => !known.has(name) && !EXTERNAL.has(name))
    expect(offenders).toEqual([])
  })
})
