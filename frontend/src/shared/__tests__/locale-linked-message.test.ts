import { describe, it, expect } from 'vitest'

// Guards against a production-only crash: vue-i18n reserves `@` for linked
// messages (`@:key`). A literal `@` in a message compiles fine in dev/test
// (the JIT compiler only warns) but throws `INVALID_LINKED_FORMAT` (code 10)
// in a production build, tearing down the view via the global ErrorBoundary.
// The only safe way to render a literal `@` is the escaped literal `{'@'}`.
const locales = import.meta.glob('/src/**/locales/*.json', { eager: true }) as Record<
  string,
  { default: Record<string, unknown> }
>

function collect(obj: Record<string, unknown>, prefix: string, out: [string, string][]): void {
  for (const [k, v] of Object.entries(obj)) {
    const path = prefix ? `${prefix}.${k}` : k
    if (typeof v === 'string') out.push([path, v])
    else if (v && typeof v === 'object') collect(v as Record<string, unknown>, path, out)
  }
}

describe('locale messages: no unescaped @ (vue-i18n linked-message hazard)', () => {
  for (const [file, mod] of Object.entries(locales)) {
    it(`${file} escapes every literal @ as {'@'}`, () => {
      const strings: [string, string][] = []
      collect(mod.default, '', strings)
      // Drop the escaped form, then any remaining `@` is an unescaped literal.
      const offenders = strings
        .filter(([, value]) => value.replace(/\{'@'\}/g, '').includes('@'))
        .map(([key]) => key)
      expect(offenders).toEqual([])
    })
  }
})
