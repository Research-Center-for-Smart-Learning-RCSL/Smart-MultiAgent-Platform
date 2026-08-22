import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'

import { i18n } from '@shared/i18n'
import { declaration, readComponentStyles, topLevelRule } from '../../../../tests/utils'
import STable, { type Column } from '../STable.vue'

const columns: Column[] = [
  { key: 'name', label: 'Name', sortable: true },
  { key: 'status', label: 'Status' },
]

function mountTable(props: Record<string, unknown> = {}) {
  return mount(STable, {
    props: { columns, data: [{ id: '1', name: 'a', status: 'ok' }], ...props },
    global: { plugins: [i18n] },
  })
}

describe('STable sticky header', () => {
  // F-8: .s-table-wrap declares overflow-x: auto, and CSS Overflow 3 §3.1
  // computes the unwritten axis to auto too, so the wrapper was the nearest
  // scrollport for the sticky thead. Nothing ever gives the wrapper a height,
  // so top: 0 was permanently satisfied and the header scrolled away with
  // main.app-shell__content, which is the real scroll owner.
  it('opts the wrapper out of its own scrollport when the header sticks', () => {
    expect(mountTable({ stickyHeader: true }).get('.s-table-wrap').classes())
      .toContain('s-table-wrap--sticky')
  })

  it('leaves the wrapper scrolling horizontally when the header does not stick', () => {
    const classes = mountTable().get('.s-table-wrap').classes()
    expect(classes).not.toContain('s-table-wrap--sticky')
    expect(classes).toEqual(['s-table-wrap'])
  })

  // Structural guard: jsdom neither implements position: sticky nor performs
  // layout, so that the header pins is T-12's (Playwright) to prove.
  it('makes the sticky wrapper transparent to overflow', () => {
    const css = readComponentStyles('shared/ui/STable.vue')

    expect(declaration(topLevelRule(css, '.s-table-wrap') ?? '', 'overflow-x')).toBe('auto')

    const sticky = topLevelRule(css, '.s-table-wrap--sticky')
    expect(sticky).not.toBeNull()
    expect(declaration(sticky as string, 'overflow')).toBe('visible')
  })
})

/** AC-11 of `docs/tasks/2026-08-21-visual-refinement-phase2-identity-and-depth`. */
describe('STable density and figures', () => {
  it('gives a row the loosened padding', () => {
    // The one deliberate layout change in phase 2, so it is pinned rather than
    // left to be re-tightened by the next person who wants more rows on screen.
    const css = readComponentStyles('shared/ui/STable.vue')
    expect(declaration(topLevelRule(css, '.s-table__td') ?? '', 'padding'))
      .toBe('var(--space-3) var(--space-4)')
    expect(declaration(topLevelRule(css, '.s-table__th') ?? '', 'padding'))
      .toBe('var(--space-3) var(--space-4)')
  })

  it('leaves header labels in the case the locale files wrote them in', () => {
    // text-transform: uppercase is inert in zh-TW, so it did not merely restyle
    // the header - it made the two locales render the same table differently.
    const css = readComponentStyles('shared/ui/STable.vue')
    expect(declaration(topLevelRule(css, '.s-table__th') ?? '', 'text-transform')).toBeNull()
    // And no tracking either: tracking applies from --font-size-xl upward, and
    // this header is --font-size-xs. Uppercase wants positive tracking;
    // sentence case at body size wants none.
    expect(declaration(topLevelRule(css, '.s-table__th') ?? '', 'letter-spacing')).toBeNull()
  })

  it('marks a number or date column for tabular figures', () => {
    const wrapper = mount(STable, {
      props: {
        columns: [
          { key: 'name', label: 'Name' },
          { key: 'count', label: 'Count', cellType: 'number' },
          { key: 'created', label: 'Created', cellType: 'date' },
        ] as Column[],
        data: [{ id: '1', name: 'a', count: 1, created: 'x' }],
      },
      global: { plugins: [i18n] },
    })

    const cells = wrapper.findAll('tbody .s-table__td')
    expect(cells).toHaveLength(3)
    expect(cells[0].classes()).not.toContain('s-table__td--figures')
    expect(cells[1].classes()).toContain('s-table__td--figures')
    expect(cells[2].classes()).toContain('s-table__td--figures')
  })

  it('applies tabular figures to the class it marks', () => {
    const css = readComponentStyles('shared/ui/STable.vue')
    expect(declaration(topLevelRule(css, '.s-table__td--figures') ?? '', 'font-variant-numeric'))
      .toBe('tabular-nums')
  })
})
