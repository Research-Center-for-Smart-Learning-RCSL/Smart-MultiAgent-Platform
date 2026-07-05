import { beforeAll, describe, expect, it } from 'vitest'

import { renderView } from '../../../../tests/utils'
import AdminPromptStudioView from '../views/AdminPromptStudioView.vue'
import { installMessages, seedConfigScope, settle } from './kit'

const routes = [
  { path: '/admin/prompt-assistant', name: 'admin.promptStudio', component: AdminPromptStudioView },
]

describe('AdminPromptStudioView', () => {
  beforeAll(installMessages)

  it('renders the platform config and templates sections', async () => {
    seedConfigScope('/admin/prompt-assistant', '/admin/prompt-templates')
    const wrapper = await renderView(AdminPromptStudioView, {
      routes,
      initialRoute: '/admin/prompt-assistant',
    })
    await settle()

    expect(wrapper.text()).toContain('Platform Prompt Assistant')
    expect(wrapper.text()).toContain('Assistant configuration')
    expect(wrapper.text()).toContain('Prompt templates')
  })
})
