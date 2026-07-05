import { describe, it, expect } from 'vitest'

// Guards against a production-breaking CSS bug: --transition-fast/normal/slow
// are duration+easing shorthands (e.g. `200ms ease`). Appending another
// easing keyword or function after the var() reference produces a value with
// two timing-functions (e.g. `200ms ease ease` or `200ms ease cubic-bezier(...)`),
// which is invalid per the `transition`/`animation` shorthand grammar — the
// browser drops the whole declaration, so the element gets no transition at
// all instead of a broken one. See docs/frontend-motion.md.
//
// Sites that need a different easing than the baked-in `ease` must pair the
// matching --duration-* (duration-only) token with an explicit easing
// instead of appending to --transition-*.
const sources = import.meta.glob(['/src/**/*.vue', '/src/**/*.css'], {
  eager: true,
  query: '?raw',
  import: 'default',
}) as Record<string, string>

describe('transition token shorthand: no easing appended after --transition-*', () => {
  const offenders: string[] = []

  for (const [file, content] of Object.entries(sources)) {
    const re = /var\(--transition-(?:fast|normal|slow)\)\s*([^,;\s])/g
    let match: RegExpExecArray | null
    while ((match = re.exec(content)) !== null) {
      offenders.push(`${file}: ...${content.slice(match.index, match.index + 60).trim()}...`)
    }
  }

  it('finds no --transition-* reference followed by another timing value', () => {
    expect(offenders).toEqual([])
  })
})
