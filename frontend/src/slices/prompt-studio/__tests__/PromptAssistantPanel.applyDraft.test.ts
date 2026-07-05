// Apply-draft flow, decoupled from socket/WS internals by mocking
// usePromptAssistantSocket directly (mirrors useGraphragSocket.test.ts's
// pattern of pre-declaring the mocked state before vi.mock).

import { beforeAll, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { ref } from 'vue'

import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { installMessages, settle } from './kit'

const messages = ref<{ role: 'user' | 'assistant'; content: string }[]>([])
const pushUserMessage = vi.fn()

vi.mock('../composables/usePromptAssistantSocket', () => ({
  usePromptAssistantSocket: () => ({
    messages,
    streamingText: ref(''),
    streaming: ref(false),
    connected: ref(true),
    errorCode: ref<string | null>(null),
    pushUserMessage,
  }),
}))

import PromptAssistantPanel from '../components/PromptAssistantPanel.vue'

function seedResolved(): void {
  server.use(
    http.get('/api/projects/proj_1/prompt-assistant', () =>
      HttpResponse.json({ available: true, source_scope: 'user', model_id: null }),
    ),
  )
}

describe('PromptAssistantPanel apply-draft', () => {
  beforeAll(installMessages)
  // jsdom doesn't implement Element.scrollTo; the panel's auto-scroll watch
  // fires as soon as messages are seeded.
  beforeAll(() => {
    Element.prototype.scrollTo = vi.fn()
  })

  it('shows Apply only for an assistant message with a fenced draft, and emits its text on click', async () => {
    seedResolved()
    messages.value = [
      { role: 'user', content: 'help me write a prompt' },
      { role: 'assistant', content: 'Sure, here:\n```\nYou are a helpful assistant.\n```' },
    ]
    const wrapper = await renderView(PromptAssistantPanel, {
      props: { projectId: 'proj_1', currentDraft: '' },
    })
    await settle()

    const applyButtons = wrapper.findAll('button').filter((b) => b.text() === 'Apply to editor')
    expect(applyButtons.length).toBe(1)

    await applyButtons[0]!.trigger('click')
    expect(wrapper.emitted('applyDraft')?.[0]).toEqual(['You are a helpful assistant.'])
  })

  it('does not show Apply for a plain assistant message with no fenced block', async () => {
    seedResolved()
    messages.value = [{ role: 'assistant', content: 'just some text, no code block' }]
    const wrapper = await renderView(PromptAssistantPanel, {
      props: { projectId: 'proj_1', currentDraft: '' },
    })
    await settle()

    const applyButtons = wrapper.findAll('button').filter((b) => b.text() === 'Apply to editor')
    expect(applyButtons.length).toBe(0)
  })
})
