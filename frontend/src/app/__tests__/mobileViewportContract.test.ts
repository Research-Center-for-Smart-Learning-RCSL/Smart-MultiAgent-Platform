import { describe, it, expect } from 'vitest'
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, relative, resolve, sep } from 'node:path'

// T-1 of docs/tasks/2026-08-19-mobile-viewport-and-breakpoints.
//
// F-39, F-25 and F-45 are three CSS declarations that no other tier in this
// repository can observe: jsdom performs no layout, implements no dynamic
// viewport unit, and resolves env(safe-area-inset-*) to nothing. A source scan
// is a blunt instrument, but the alternative here is no guard at all.
//
// Shaped after viewRoots.test.ts, including its two load-bearing properties:
// the tree is read from disk rather than imported (so this crosses no slice
// boundary), and every sweep opens with a count assertion so a glob that
// silently stops matching fails loudly instead of passing vacuously.

// vitest.config's root is `frontend/`; `import.meta.url` is not a file URL
// under the jsdom environment, so resolve from the run root instead.
const ROOT = process.cwd()
const SRC = resolve(ROOT, 'src')

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry)
    if (statSync(full).isDirectory()) walk(full, out)
    else if (entry.endsWith('.vue') || entry.endsWith('.css')) out.push(full)
  }
  return out
}

// Tests legitimately name the values these sweeps forbid — AgentDetailView's
// T-9 pins the absence of a superseded `100vh` constant by spelling it out.
const styleFiles = walk(SRC).filter((f) => !f.includes(`${sep}__tests__${sep}`))

function read(file: string): string {
  return readFileSync(file, 'utf8')
}

/**
 * Source with `/* *\/` and `<!-- -->` comments blanked out.
 *
 * Every rule below is a rule about a *declaration*, and the comment that
 * explains why a declaration is written the way it is has to be free to name
 * the spelling it rejects. Without this, documenting `dvh, not 100vh` next to
 * the fix would fail the sweep that the fix exists to satisfy.
 */
function declarations(source: string): string {
  return source.replace(/\/\*[\s\S]*?\*\//g, ' ').replace(/<!--[\s\S]*?-->/g, ' ')
}

function sweep(predicate: (source: string) => string[]): string[] {
  return styleFiles
    .map((file) => ({ file, found: predicate(declarations(read(file))) }))
    .filter((r) => r.found.length > 0)
    .map((r) => `${relative(SRC, r.file)} (${r.found.join(', ')})`)
}

describe('mobile viewport contract', () => {
  it('finds the files it is meant to sweep', () => {
    expect(styleFiles.length).toBeGreaterThan(200)
  })

  // -- T-1(a), F-39 ---------------------------------------------------------
  // useBreakpoint.ts:5 and main.css:57-60 both declare the thresholds as
  // MIN-widths, so a mobile-side rule must stop one pixel below: 479 and 767.
  // At exactly 480 or 768 an inclusive rule applies the smaller layout while
  // useBreakpoint() reports the larger one, and the two disagree.
  it('stops max-width media queries one pixel below each breakpoint', () => {
    // Only the query PRELUDE, never a declaration body. `.form-card
    // { max-width: 480px }` is an element cap that happens to share the
    // number: rewriting it to 479 would resize a card to satisfy a rule about
    // breakpoints. Five such caps live in the identity views today.
    const PRELUDE = /@media[^{]*/g
    const INCLUSIVE = /max-width:\s*(?:480|768)px/g

    const offenders = sweep((source) =>
      [...source.matchAll(PRELUDE)].flatMap((q) => q[0].match(INCLUSIVE) ?? []),
    )

    expect(offenders).toEqual([])
  })

  // -- T-1(c), F-45 ---------------------------------------------------------
  // `vh` resolves against the LARGE viewport, so a 100vh shell is taller than
  // the visible area by the browser toolbar height and its bottom grid row is
  // below the fold on first paint (02-layout-shell.md:111-116). It also breaks
  // useVisualViewport's arithmetic, which measures against window.innerHeight:
  // see that composable's note, and the dossier's §5 derivation.
  it('sizes every viewport-height declaration against the dynamic viewport', () => {
    const offenders = sweep((source) => [...source.matchAll(/100vh/g)].map((m) => m[0]))

    expect(offenders).toEqual([])
  })

  it('gives the app shell a dynamic viewport height', () => {
    expect(declarations(read(resolve(SRC, 'app/App.vue')))).toContain('min-height: 100dvh')
  })

  // The sweep above cannot see the one place that was still shipping `100vh`.
  // It reads .vue and .css under src/ with __tests__/ excluded, because a test
  // legitimately names the value it forbids - but Tailwind scans the whole
  // project, every extension and comments included, so a class name written in
  // a .ts test is a real rule in dist/assets/*.css. That is how
  // `AgentDetailView.test.ts`'s two `not.toContain` assertions kept a live
  // `height:calc(100vh - 8rem)` rule in the bundle: the only file still
  // emitting the unit was the only file the guard was blind to.
  //
  // This asks a narrower question that needs no exclusions: does any Tailwind
  // arbitrary value - the bracketed form, the only spelling that becomes a rule
  // - name the static viewport unit? Prose and bare strings are untouched, so a
  // test can still assert absence and a comment can still explain why.
  it('lets no arbitrary-value utility name the static viewport unit', () => {
    const SCANNED = /\.(vue|ts|css|html)$/
    const SKIP = new Set(['node_modules', 'dist', 'coverage', 'playwright-report', 'test-results'])

    function walkAll(dir: string, out: string[] = []): string[] {
      for (const entry of readdirSync(dir)) {
        if (SKIP.has(entry) || entry.startsWith('.')) continue
        const full = join(dir, entry)
        if (statSync(full).isDirectory()) walkAll(full, out)
        else if (SCANNED.test(entry)) out.push(full)
      }
      return out
    }

    const scanned = walkAll(ROOT)
    expect(scanned.length).toBeGreaterThan(200)

    // A utility name has to precede the bracket. Without that anchor the sweep
    // matches any bracketed text and reports its own `matchAll(/100vh/g)` -
    // which is not a candidate and emits nothing, as removing the class literal
    // from AgentDetailView.test.ts proved: the rule left the bundle while that
    // expression stayed.
    const ARBITRARY_VALUE = /[a-z][a-z0-9:-]*-\[[^\]\n]*100vh[^\]\n]*\]/g

    const offenders = scanned
      .map((file) => ({ file, found: read(file).match(ARBITRARY_VALUE) ?? [] }))
      .filter((r) => r.found.length > 0)
      .map((r) => `${relative(ROOT, r.file)} (${r.found.join(', ')})`)

    expect(offenders).toEqual([])
  })

  // -- T-1(b), F-25 ---------------------------------------------------------
  // The two halves are mutually dependent in one direction only, which is the
  // trap: insets without the meta are inert, but the meta without insets is
  // actively harmful — it removes the browser's own inset from every surface
  // at once. Asserting both together is what makes shipping half impossible.
  describe('safe areas', () => {
    // The complete set of present-day elements that touch a viewport edge
    // (dossier Q-5), each with the edges it actually meets. PublicLayout is
    // deliberately absent: it adds no padding and only wraps Landing, which
    // carries its own gutters.
    //
    // Per-edge, not per-file. A post-close review found that asserting only
    // that `env(safe-area-inset-` appears SOMEWHERE in a file let Landing pass
    // while insetting two edges of the four it needed - its nav was rendering
    // under the status bar. A surface that protects half of itself is exactly
    // the failure this sweep exists to catch.
    const INSET_SURFACES: Record<string, string[]> = {
      // No 'top': the shell's top inset is carried by --topbar-height-total
      // (declared in main.css), so the topbar track grows into the strip
      // rather than the shell padding itself away from it.
      'app/layouts/AppShell.vue': ['left', 'right', 'bottom'],
      'app/components/AppTopBar.vue': ['top'],
      'app/layouts/AuthLayout.vue': ['top', 'left', 'right', 'bottom'],
      // No bottom: the landing page scrolls the document, so its footer is
      // reachable past the home indicator.
      'app/views/Landing.vue': ['top', 'left', 'right'],
      'shared/ui/SDrawer.vue': ['top', 'left', 'right', 'bottom'],
      'shared/ui/SModal.vue': ['top', 'left', 'right', 'bottom'],
      // Fixed, outside every layout's padding box, and the first tab stop.
      'shared/styles/main.css': ['top', 'left'],
      'shared/ui/SNetworkBanner.vue': ['top'],
    }

    it('opts the document into the display cutout', () => {
      expect(declarations(read(resolve(ROOT, 'index.html')))).toContain('viewport-fit=cover')
    })

    it('insets every edge of every surface that meets one', () => {
      const offenders = Object.entries(INSET_SURFACES).flatMap(([rel, edges]) => {
        const source = declarations(read(resolve(SRC, rel)))
        return edges
          .filter((edge) => !source.includes(`env(safe-area-inset-${edge}`))
          .map((edge) => `${rel} (${edge})`)
      })

      expect(offenders).toEqual([])
    })

    // Q-6: env() with a fallback degrades to the fallback on any engine that
    // does not know the variable and to 0px on any device without a cutout, so
    // a bare `env(safe-area-inset-x)` would collapse a designed gutter to
    // nothing. The max() form is what preserves it.
    it('writes every inset with a fallback so a revert restores today exactly', () => {
      const offenders = Object.keys(INSET_SURFACES).filter((rel) =>
        [
          ...declarations(read(resolve(SRC, rel))).matchAll(
            /env\(safe-area-inset-[a-z]+([^)]*)\)/g,
          ),
        ].some((m) => !m[1].includes(',')),
      )

      expect(offenders).toEqual([])
    })
  })
})
