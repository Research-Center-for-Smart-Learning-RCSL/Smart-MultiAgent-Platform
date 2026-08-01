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
