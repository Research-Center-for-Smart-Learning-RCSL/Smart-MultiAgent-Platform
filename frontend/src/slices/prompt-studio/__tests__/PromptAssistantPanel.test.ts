import { afterEach, beforeAll, describe, expect, it, vi } from 'vitest'
import { http, HttpResponse } from 'msw'

import type * as Transport from '@shared/transport'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import { installMessages, settle } from './kit'

// The button re-enable test below needs to drive a deterministic
// connect/reconnect; every other test in this file uses the real transport
// (which never gets exercised, since sessionId stays null until send() is
// called). Preserves every other export via importOriginal so `send()`'s
// isProblemWithType usage keeps working.
const subscribedHandlers: Array<(ev: Transport.ChannelEvent) => void> = []
const statusHandlers: Array<(connected: boolean) => void> = []

vi.mock('@shared/transport', async (importOriginal) => {
  const actual = await importOriginal<typeof Transport>()
  const channel = {
    subscribe: (_name: string, handler: (ev: Transport.ChannelEvent) => void) => {
      subscribedHandlers.push(handler)
      return () => {}
    },
    onStatus: (handler: (connected: boolean) => void) => {
      statusHandlers.push(handler)
      return () => {}
    },
    connect: () => {},
    disconnect: () => {},
    close: () => {},
  }
  return { ...actual, wsManager: { channel: () => channel, close: () => {} } }
})

import PromptAssistantPanel from '../components/PromptAssistantPanel.vue'

function emitToken(text: string): void {
  for (const h of [...subscribedHandlers]) h({ type: 'prompt.token', text } as Transport.ChannelEvent)
}

function emitStatus(connected: boolean): void {
  for (const h of [...statusHandlers]) h(connected)
}

function seedResolved(available: boolean): void {
  server.use(
    http.get('/api/projects/proj_1/prompt-assistant', () =>
      HttpResponse.json({ available, source_scope: available ? 'user' : null, model_id: null }),
    ),
  )
}

describe('PromptAssistantPanel', () => {
  beforeAll(installMessages)

  it('shows the unavailable state when no assistant resolves', async () => {
    seedResolved(false)
    const wrapper = await renderView(PromptAssistantPanel, {
      props: { projectId: 'proj_1', currentDraft: '' },
    })
    await settle()
    expect(wrapper.text()).toContain('No prompt assistant is configured for this project.')
  })

  it('shows the composer and intro when an assistant is available', async () => {
    seedResolved(true)
    const wrapper = await renderView(PromptAssistantPanel, {
      props: { projectId: 'proj_1', currentDraft: '' },
    })
    await settle()
    expect(wrapper.text()).toContain('Ask the assistant to help draft')
    // Composer send button is present.
    expect(wrapper.text()).toContain('Send')
  })
})

describe('PromptAssistantPanel reconnect recovery (F-13 fix, AC-3)', () => {
  beforeAll(installMessages)
  // jsdom doesn't implement Element.scrollTo; the panel's auto-scroll watch
  // fires once the reconciled reply is pushed into `messages`.
  beforeAll(() => {
    Element.prototype.scrollTo = vi.fn()
  })

  afterEach(() => {
    subscribedHandlers.length = 0
    statusHandlers.length = 0
  })

  it('re-enables the Send button after a mid-turn reconnect, via a real getSession round-trip', async () => {
    seedResolved(true)
    server.use(
      http.post('/api/projects/proj_1/prompt-assistant/sessions', () =>
        HttpResponse.json({ session_id: 'sess_1' }, { status: 201 }),
      ),
      http.post('/api/prompt-assistant/sessions/sess_1/messages', () => new HttpResponse(null, { status: 202 })),
      http.get('/api/prompt-assistant/sessions/sess_1', () =>
        HttpResponse.json({ session_id: 'sess_1', messages: [{ role: 'user', content: 'hi', error: false }] }),
      ),
    )

    const wrapper = await renderView(PromptAssistantPanel, {
      props: { projectId: 'proj_1', currentDraft: '' },
    })
    await settle()

    await wrapper.find('textarea').setValue('hi')
    await wrapper.find('button').trigger('click')
    await settle()

    emitToken('partial reply')
    await settle()
    // send() clears the input on success, so re-fill it: `:disabled` is
    // `!input.trim() || sending || streaming` and this assertion must isolate
    // the `streaming` term, not an incidentally-empty composer.
    await wrapper.find('textarea').setValue('a follow-up')
    expect(wrapper.find('button').attributes('disabled')).toBeDefined()

    // Socket drops mid-turn and reconnects; the terminal frame is lost. The
    // composable's onStatus handler clears `streaming` and refetches the
    // session over the REAL api-client -> axios -> MSW round-trip (not a
    // mocked '../api' module, unlike usePromptAssistantSocket.test.ts, which
    // pins the reconcile/generation-guard logic in isolation instead).
    emitStatus(false)
    emitStatus(true)
    await settle()

    expect(wrapper.find('button').attributes('disabled')).toBeUndefined()
  })
})
