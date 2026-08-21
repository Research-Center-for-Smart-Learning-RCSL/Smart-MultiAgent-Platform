import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ChatroomSearchPanel from '../components/ChatroomSearchPanel.vue'
import { sanitizeSnippet } from '../utils/renderMarkdown'
import type { SearchHit } from '../types'

// F-22: this is the only test that exercises the whole highlight chain --
// the `<mark>` delimiters `ts_headline` emits, through the real sanitiser,
// through the `v-html` binding, to an element the panel's `:deep(mark)` rule
// can actually style. Asserting on `sanitizeSnippet` alone would not catch a
// panel that never renders the snippet.

function hit(snippet: string): SearchHit {
  return {
    message_id: 'm1',
    sender_type: 'user',
    sender_id: 'u1',
    created_at: '2026-01-01T00:00:00Z',
    snippet,
    rank: 0.5,
  }
}

function mount(hits: SearchHit[]) {
  return renderView(ChatroomSearchPanel, {
    props: {
      query: 'revenue',
      hits,
      // Built exactly as useChatroomSearch builds it, with the real sanitiser.
      renderedSnippets: Object.fromEntries(
        hits.map((h) => [h.message_id, sanitizeSnippet(h.snippet)]),
      ),
      searching: false,
    },
  })
}

describe('ChatroomSearchPanel highlighting', () => {
  it('renders a mark element for a highlighted hit', async () => {
    const wrapper = await mount([hit('quarterly <mark>revenue</mark> note')])

    const marked = wrapper.find('.result__snippet mark')
    expect(marked.exists()).toBe(true)
    expect(marked.text()).toBe('revenue')
  })

  it('keeps the surrounding snippet text intact', async () => {
    const wrapper = await mount([hit('quarterly <mark>revenue</mark> note')])

    expect(wrapper.find('.result__snippet').text()).toContain('quarterly')
    expect(wrapper.find('.result__snippet').text()).toContain('note')
  })

  it('does not render script content smuggled through a snippet', async () => {
    const wrapper = await mount([hit('<mark>hi</mark><script>alert(1)</script>')])

    expect(wrapper.find('.result__snippet').html()).not.toContain('<script')
  })
})

// T-10 (search half) of docs/tasks/2026-08-19-chatroom-scroll-and-composer,
// F-48. The panel is mounted inside `.chatroom__feed`, which is grid-row 2 of
// `48px 1fr auto auto` and is the positioned ancestor -- so its top edge is
// already below the header. A `top: 48px` on the panel counted the header a
// second time and left a bare strip of feed above it.
//
// Asserted against the stylesheet source because jsdom applies no scoped
// styles and computes no layout; the idiom is
// shared/__tests__/transition-token-shorthand.test.ts. Where the box actually
// lands is AC-16's browser half.
const panelSource = Object.values(
  import.meta.glob('/src/slices/conversation/components/ChatroomSearchPanel.vue', {
    eager: true,
    query: '?raw',
    import: 'default',
  }) as Record<string, string>,
)[0]

describe('ChatroomSearchPanel position (F-48)', () => {
  const rule = /\.search-panel\s*\{([^}]*)\}/.exec(panelSource ?? '')?.[1] ?? ''

  it('reads its own rule out of the SFC, so the assertions below mean something', () => {
    expect(panelSource).toBeTypeOf('string')
    expect(rule).toContain('position: absolute')
  })

  it('sits flush with the top of its containing block', () => {
    expect(/top:\s*0\s*;/.test(rule)).toBe(true)
  })

  it('does not re-apply the header height its containing block already excludes', () => {
    expect(rule).not.toMatch(/top:\s*48px/)
  })
})
