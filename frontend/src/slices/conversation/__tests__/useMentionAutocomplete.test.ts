import { describe, it, expect } from 'vitest'
import { ref } from 'vue'

import { useMentionAutocomplete } from '../composables/useMentionAutocomplete'

const AGENTS = [
  { id: 'a1', name: 'Researcher' },
  { id: 'a2', name: 'Data Analyst' },
]

function setup(value: string, caret: number) {
  const el = {
    value,
    selectionStart: caret,
    focus: () => {},
    setSelectionRange: () => {},
  } as unknown as HTMLTextAreaElement
  const inserted: string[] = []
  const m = useMentionAutocomplete({
    textarea: ref(el),
    agents: () => AGENTS,
    onInsert: (v) => inserted.push(v),
  })
  m.refresh()
  return { m, inserted }
}

function key(overrides: Partial<KeyboardEvent>): KeyboardEvent {
  return { preventDefault: () => {}, ...overrides } as KeyboardEvent
}

describe('useMentionAutocomplete', () => {
  it('opens with matches for an in-progress @token', () => {
    const { m } = setup('hi @Re', 6)
    expect(m.open.value).toBe(true)
    expect(m.matches.value.map((a) => a.id)).toEqual(['a1'])
  })

  it('inserts the highlighted match on Enter', () => {
    const { m, inserted } = setup('hi @Re', 6)
    const consumed = m.handleKeydown(key({ key: 'Enter', shiftKey: false, isComposing: false }))
    expect(consumed).toBe(true)
    expect(inserted[0]).toBe('hi @Researcher ')
  })

  it('does not hijack keys during IME composition', () => {
    const { m, inserted } = setup('hi @Re', 6)
    expect(m.open.value).toBe(true)
    const consumed = m.handleKeydown(key({ key: 'Enter', shiftKey: false, isComposing: true }))
    expect(consumed).toBe(false)
    expect(inserted).toEqual([])
  })

  it('closes when the caret is not inside a mention', () => {
    const { m } = setup('no mention here', 5)
    expect(m.open.value).toBe(false)
  })
})
