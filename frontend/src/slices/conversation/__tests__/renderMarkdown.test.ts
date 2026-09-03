import { describe, it, expect, vi } from 'vitest'
import { enhanceRenderedMarkdown, renderMarkdown, sanitizeSnippet } from '../utils/renderMarkdown'

const mermaidRender = vi.fn(async (id: string) => ({ svg: `<svg id="${id}"></svg>` }))

vi.mock('mermaid', () => ({
  default: { initialize: vi.fn(), render: (id: string, text: string) => mermaidRender(id, text) },
}))

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

// `mermaid.render` creates and removes its temporary element by id, so two
// concurrent renders sharing one id collide and one throws into the pass's empty
// catch — leaving that diagram a raw fence, which is the symptom F-9 exists to
// fix. The id was `Date.now()` plus the node's index within one root, unique only
// while the app had a single enhancement root. ObservationCard now mounts one per
// card, all in the same tick behind the same debounce.
describe('enhanceRenderedMarkdown mermaid ids', () => {
  function rootWithFence(): HTMLElement {
    const root = document.createElement('div')
    root.innerHTML = '<pre><code class="language-mermaid">graph TD; a--&gt;b;</code></pre>'
    return root
  }

  it('gives every diagram a distinct id across roots enhanced in the same millisecond', async () => {
    // The clock is frozen rather than the calls made concurrent: two cards
    // mounting together share one debounce, so the real collision was two roots
    // reading the same `Date.now()` and both starting at index 0. Freezing it
    // reproduces that exactly and deterministically — driving it with real
    // concurrency instead raced vitest's dynamic-mock loading and proved nothing.
    mermaidRender.mockClear()
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_700_000_000_000)

    await enhanceRenderedMarkdown(rootWithFence())
    await enhanceRenderedMarkdown(rootWithFence())

    const ids = mermaidRender.mock.calls.map(([id]) => id)
    expect(ids).toHaveLength(2)
    expect(new Set(ids).size).toBe(2)

    now.mockRestore()
  })

  it('gives every diagram in one root a distinct id', async () => {
    mermaidRender.mockClear()
    const root = document.createElement('div')
    root.innerHTML =
      '<pre><code class="language-mermaid">a</code></pre><pre><code class="language-mermaid">b</code></pre>'

    await enhanceRenderedMarkdown(root)

    const ids = mermaidRender.mock.calls.map(([id]) => id)
    expect(ids).toHaveLength(2)
    expect(new Set(ids).size).toBe(2)
  })
})
