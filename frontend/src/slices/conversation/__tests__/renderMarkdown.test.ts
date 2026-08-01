import { describe, it, expect } from 'vitest'
import { renderMarkdown, sanitizeSnippet } from '../utils/renderMarkdown'

// F-22: the search snippet arrives from `ts_headline` with `<mark>` delimiters
// around each hit. `sanitizeSnippet` must keep that one element while staying
// exactly as strict as it is today about everything else -- the snippet is a
// fragment of a user's raw markdown, so DOMPurify is the only thing between it
// and the `v-html` binding in ChatroomSearchPanel.

describe('sanitizeSnippet', () => {
  it('preserves the mark element the backend uses to delimit a hit', () => {
    expect(sanitizeSnippet('a <mark>b</mark> c')).toContain('<mark>b</mark>')
  })

  it('strips attributes from a mark element, including event handlers', () => {
    const out = sanitizeSnippet('<mark onclick="alert(1)" style="color:red">x</mark>')

    expect(out).toContain('<mark>x</mark>')
    expect(out).not.toContain('onclick')
    expect(out).not.toContain('style')
  })

  it.each([
    ['script tags', '<script>alert(1)</script>hi', ['<script', 'alert(1)']],
    ['inline handlers', '<img src="x" onerror="alert(1)">', ['onerror']],
    ['frames', '<iframe src="https://evil.test"></iframe>', ['<iframe']],
    ['inline styles', '<b style="position:fixed">x</b>', ['style']],
    ['form controls', '<form action="/x"><input name="p"></form>', ['<form']],
    ['javascript URLs', '<a href="javascript:alert(1)">x</a>', ['javascript:']],
  ])('still strips %s', (_label, input, forbidden) => {
    const out = sanitizeSnippet(input)

    for (const needle of forbidden) expect(out).not.toContain(needle)
  })

  it('returns an empty string for nullish input', () => {
    expect(sanitizeSnippet('')).toBe('')
  })
})

describe('renderMarkdown', () => {
  it('does not allow mark, so widening the snippet config did not widen message bodies', () => {
    expect(renderMarkdown('<mark>x</mark>')).not.toContain('<mark>')
  })

  it('keeps the tags message bodies rely on', () => {
    const out = renderMarkdown('**bold** and `code`')

    expect(out).toContain('<strong>')
    expect(out).toContain('<code>')
  })

  it('strips scripts from message bodies', () => {
    expect(renderMarkdown('<script>alert(1)</script>text')).not.toContain('<script')
  })
})
