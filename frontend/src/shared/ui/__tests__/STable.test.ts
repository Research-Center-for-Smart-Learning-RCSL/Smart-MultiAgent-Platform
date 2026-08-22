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

/**
 * Phase 3's V-7. Before this, a sortable header carried `@click` and
 * `cursor: pointer` and nothing else - no tabindex, no key handler, no role -
 * so sorting a table was reachable by pointer only while `aria-sort` announced
 * a control that could not be operated.
 */
describe('STable sortable headers are operable by keyboard', () => {
  it('renders a real button for a sortable column and a span for a plain one', () => {
    const heads = mountTable().findAll('thead .s-table__th-content')
    expect(heads).toHaveLength(2)
    expect(heads[0].element.tagName).toBe('BUTTON')
    expect(heads[0].attributes('type')).toBe('button')
    // A non-sortable header must NOT become a button: it would join the tab
    // order and announce itself as a control that does nothing.
    expect(heads[1].element.tagName).toBe('SPAN')
    expect(heads[1].attributes('type')).toBeUndefined()
  })

  it('sorts from the button and from the cell around it, but only once', async () => {
    const fromButton = mountTable()
    await fromButton.get('thead .s-table__th-content').trigger('click')
    expect(fromButton.emitted('sort')?.[0]).toEqual([{ key: 'name', order: 'asc' }])

    // The cell keeps its own handler so the pointer target stays the whole
    // cell, gutters included - moving the control into the button would
    // otherwise have shrunk it to the label and its icon.
    const fromCell = mountTable()
    await fromCell.get('thead .s-table__th').trigger('click')
    expect(fromCell.emitted('sort')?.[0]).toEqual([{ key: 'name', order: 'asc' }])
  })

  it('does not sort twice when the click lands on the button', async () => {
    // `@click.stop` on the button is the only thing preventing this: without it
    // the event bubbles into the cell's handler, the order toggles a second
    // time, and clicking a header appears to do nothing at all.
    const wrapper = mountTable()
    const button = wrapper.get('thead .s-table__th-content')
    expect(button.element.tagName).toBe('BUTTON')
    await button.trigger('click')
    expect(wrapper.emitted('sort')).toHaveLength(1)
  })

  it('leaves the button visually indistinguishable from the span it replaced', () => {
    // A button brings its own font, background, border and padding. If any of
    // them survive, sortable and non-sortable headers stop matching.
    const rule = topLevelRule(readComponentStyles('shared/ui/STable.vue'), '.s-table__th-content')
    expect(rule).not.toBeNull()
    for (const [prop, value] of [
      ['background', 'none'],
      ['border', 'none'],
      ['font', 'inherit'],
      ['color', 'inherit'],
      ['padding', '0'],
      ['margin', '0'],
    ] as const) {
      expect(declaration(rule as string, prop), `${prop} is not reset`).toBe(value)
    }
  })
})
