import { beforeAll, describe, expect, it } from 'vitest'

import { renderView } from '../../../../tests/utils'
import PersonalPromptStudioView from '../views/PersonalPromptStudioView.vue'
import { installMessages, seedConfigScope, settle } from './kit'

const routes = [
  { path: '/account/prompt-assistant', name: 'prompt-studio.personal', component: PersonalPromptStudioView },
]

describe('PersonalPromptStudioView', () => {
  beforeAll(installMessages)

  it('renders the personal config and templates sections', async () => {
    seedConfigScope('/me/prompt-assistant', '/me/prompt-templates')
    const wrapper = await renderView(PersonalPromptStudioView, {
      routes,
      initialRoute: '/account/prompt-assistant',
    })
    await settle()

    expect(wrapper.text()).toContain('Prompt Assistant')
    expect(wrapper.text()).toContain('Assistant configuration')
    expect(wrapper.text()).toContain('Prompt templates')
  })
})
