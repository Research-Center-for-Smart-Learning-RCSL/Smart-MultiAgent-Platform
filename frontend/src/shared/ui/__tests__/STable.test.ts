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
