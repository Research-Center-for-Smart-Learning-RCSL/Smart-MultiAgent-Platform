import { describe, it, expect } from 'vitest'

import { resolveMentions, activeMention } from '../utils/mentions'

const agents = [
  { id: 'a1', name: 'Researcher' },
  { id: 'a2', name: 'Data Analyst' },
  { id: 'a3', name: 'Data' },
]

describe('resolveMentions', () => {
  it('returns nothing without an @ or without agents', () => {
    expect(resolveMentions('hello world', agents)).toEqual([])
    expect(resolveMentions('@Researcher', [])).toEqual([])
  })

  it('matches a single mention case-insensitively', () => {
    expect(resolveMentions('hey @researcher can you look', agents)).toEqual(['a1'])
  })

  it('matches names containing spaces', () => {
    expect(resolveMentions('ping @Data Analyst please', agents)).toEqual(['a2'])
  })

  it('does not treat email-style text as a mention', () => {
    expect(resolveMentions('mail me at foo@Researcher.com', agents)).toEqual([])
  })

  it('does not match a name that is only a prefix of a longer token', () => {
    // "@Database" must not resolve the "Data" agent.
    expect(resolveMentions('check @Database now', agents)).toEqual([])
  })

  it('resolves multiple distinct mentions in agent order', () => {
    expect(resolveMentions('@Data and @Researcher', agents)).toEqual(['a1', 'a3'])
  })
})

describe('activeMention', () => {
  it('detects an in-progress token at the caret', () => {
    const text = 'hello @Res'
    expect(activeMention(text, text.length)).toEqual({ start: 6, query: 'Res' })
  })

  it('detects a bare @ with an empty query', () => {
    const text = 'hi @'
    expect(activeMention(text, text.length)).toEqual({ start: 3, query: '' })
  })

  it('returns null when the caret is not inside a mention', () => {
    expect(activeMention('hello @Res done', 'hello @Res done'.length)).toBeNull()
    expect(activeMention('no mention here', 5)).toBeNull()
  })
})
