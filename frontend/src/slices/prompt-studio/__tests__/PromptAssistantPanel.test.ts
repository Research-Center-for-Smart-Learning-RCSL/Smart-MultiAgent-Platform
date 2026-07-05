import { beforeAll, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'

import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import PromptAssistantPanel from '../components/PromptAssistantPanel.vue'
import { installMessages, settle } from './kit'

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
