import { http, HttpResponse } from 'msw'
import { beforeAll, describe, expect, it } from 'vitest'

import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AdminPromptStudioView from '../views/AdminPromptStudioView.vue'
import { installMessages, settle } from './kit'

const routes = [
  { path: '/admin/prompt-assistant', name: 'admin.promptStudio', component: AdminPromptStudioView },
  { path: '/admin/prompt-assistant/presets', name: 'admin.promptPresets', component: { template: '<div />' } },
]

describe('AdminPromptStudioView', () => {
  beforeAll(installMessages)

  it('renders only the platform templates section, with a link to persona presets', async () => {
    server.use(
      http.get('/api/admin/prompt-templates', () => HttpResponse.json([])),
    )
    const wrapper = await renderView(AdminPromptStudioView, {
      routes,
      initialRoute: '/admin/prompt-assistant',
    })
    await settle()

    expect(wrapper.text()).toContain('Platform Prompt Assistant')
    expect(wrapper.text()).toContain('Prompt templates')
    // The singleton platform config form is gone -- superseded by presets.
    expect(wrapper.text()).not.toContain('Assistant configuration')
    expect(wrapper.text()).toContain('Manage persona presets')
  })
})
