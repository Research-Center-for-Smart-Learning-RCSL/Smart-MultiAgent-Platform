import { describe, it, expect } from 'vitest'

/**
 * AC-10 of `docs/tasks/2026-08-21-visual-refinement-phase2-identity-and-depth`.
 *
 * `--color-border` used to draw three different things - the outline of a text
 * field, the edge of a card, and the rule between two table rows - so no weight
 * could be right for any of them. The split is only worth anything if it stays
 * split, and a role is not something a value can carry on its own: nothing about
 * `#cbd5e1` says "container edge". This asserts the assignment.
 *
 * The sweep is deliberately over the raw source. A rule's role is a property of
 * where it is written, and the built CSS has already thrown that away.
 */
const sources = import.meta.glob('/src/**/*.vue', { eager: true, query: '?raw', import: 'default' }) as Record<
  string,
  string
>

type Use = { file: string; line: string; token: string }

const BORDER_TOKEN = /var\(\s*(--color-border(?:-strong|-subtle)?)\s*\)/

function uses(): Use[] {
  const out: Use[] = []
  for (const [path, text] of Object.entries(sources)) {
    const file = path.split('/').pop() as string
    if (path.includes('__tests__/')) continue
    for (const line of text.split('\n')) {
      const m = BORDER_TOKEN.exec(line)
      if (m) out.push({ file, line: line.trim(), token: m[1] })
    }
  }
  return out
}

const ALL = uses()

/**
 * Components whose boundary IS the affordance: with the outline removed there
 * is nothing left to say a control is present. WCAG 2.1 1.4.11 applies to
 * exactly this set, and contrast.test.ts holds --color-border-strong to 3:1.
 *
 * Buttons are deliberately absent. A button carries a label and a fill, so its
 * border is decoration, not identification - which is why SButton's secondary
 * variant stays on --color-border.
 */
const CONTROL_COMPONENTS = [
  'SInput.vue',
  'SSelect.vue',
  'STextarea.vue',
  'SSearchInput.vue',
  'SCheckbox.vue',
  'SRadio.vue',
  'SToggle.vue',
  'SFileUpload.vue',
  'SCodeEditor.vue',
]

/**
 * Interior separators named by §6.4 of the dossier. Keyed by file and by the
 * declaration's own text so that moving a rule inside its file does not break
 * the assertion, but changing what it draws does.
 */
const INTERIOR_SITES: [file: string, fragment: string][] = [
  ['STable.vue', 'border-bottom'],
  ['SCard.vue', 'border-bottom'],
  ['SCard.vue', 'border-top'],
  ['SAccordion.vue', 'border-bottom'],
  ['SDropdown.vue', 'background'],
  ['SDivider.vue', 'background-color'],
  ['AppSidebar.vue', 'background-color'],
]

describe('border roles', () => {
  it('swept a plausible amount of source', () => {
    // Guards against the glob silently matching nothing, which would make every
    // assertion below pass without reading a single file.
    expect(Object.keys(sources).length).toBeGreaterThan(200)
    expect(ALL.length).toBeGreaterThan(100)
  })

  it('uses all three weights', () => {
    for (const token of ['--color-border', '--color-border-strong', '--color-border-subtle']) {
      expect(
        ALL.filter((u) => u.token === token).length,
        `${token} has no consumer, so its role is undefined in practice`,
      ).toBeGreaterThan(0)
    }
  })

  it.each(CONTROL_COMPONENTS)('%s draws its control boundary at the strong weight', (file) => {
    const inFile = ALL.filter((u) => u.file === file)
    expect(inFile.length, `${file} states no border at all`).toBeGreaterThan(0)
    expect(
      inFile.some((u) => u.token === '--color-border-strong'),
      `${file} is a form control: its boundary is the only thing identifying it, ` +
        `so it must read --color-border-strong (WCAG 2.1 1.4.11)`,
    ).toBe(true)
  })

  it.each(INTERIOR_SITES)('%s draws its %s rule at the interior weight', (file, fragment) => {
    const matching = ALL.filter((u) => u.file === file && u.line.startsWith(fragment))
    expect(matching.length, `${file} has no ${fragment} border rule`).toBeGreaterThan(0)
    for (const u of matching) {
      expect(u.token, `${file}: ${u.line}`).toBe('--color-border-subtle')
    }
  })

  it('never draws a divide-* utility at a boundary weight', () => {
    // `divide-*` separates siblings inside one container. It is an interior
    // rule by construction, so this needs no per-site judgement - which makes
    // it the one part of the assignment a future change cannot get wrong.
    const offenders = ALL.filter(
      (u) => /\bdivide(-[xy])?-\[var\(--color-border\)\]/.test(u.line) || /\bdivide-\[var\(--color-border\)\]/.test(u.line),
    )
    expect(offenders.map((u) => `${u.file}: ${u.line}`)).toEqual([])
  })
})
