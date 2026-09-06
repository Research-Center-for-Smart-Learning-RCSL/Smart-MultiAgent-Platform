import { http, HttpResponse } from 'msw'
import { beforeAll, describe, expect, it } from 'vitest'

import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AdminPromptPresetListView from '../views/AdminPromptPresetListView.vue'
import { installMessages, settle } from './kit'

const routes = [
  {
    path: '/admin/prompt-assistant/presets',
    name: 'admin.promptPresets',
    component: AdminPromptPresetListView,
  },
  { path: '/admin/prompt-assistant/presets/new', name: 'admin.promptPresetNew', component: { template: '<div />' } },
  {
    path: '/admin/prompt-assistant/presets/:presetId',
    name: 'admin.promptPresetEdit',
    component: { template: '<div />' },
  },
]

const presets = [
  {
    id: 'cfg_1',
    scope: 'platform',
    name: 'General Prompt Assistant',
    description: 'Seeded from the prompt-assistant agent pack.',
    enabled: false,
    persona_prompt: 'p',
    system_prompt: '',
    key_id: null,
    key: null,
    key_revoked: false,
    model_id: null,
    daily_request_limit_per_user: 50,
    hide_platform_templates: false,
    version: 1,
    files: [],
  },
  {
    id: 'cfg_2',
    scope: 'platform',
    name: 'Creative Thinking Defense Prompt Assistant',
    description: 'Seeded from the creative-thinking-prompt-defense agent pack.',
    enabled: true,
    persona_prompt: 'p2',
    system_prompt: '',
    key_id: null,
    key: null,
    key_revoked: false,
    model_id: null,
    daily_request_limit_per_user: 50,
    hide_platform_templates: false,
    version: 1,
    files: [],
  },
]

describe('AdminPromptPresetListView', () => {
  beforeAll(installMessages)

  it('lists every platform preset with its name, description, and active status', async () => {
    server.use(http.get('/api/admin/prompt-assistant/presets', () => HttpResponse.json(presets)))

    const wrapper = await renderView(AdminPromptPresetListView, {
      routes,
      initialRoute: '/admin/prompt-assistant/presets',
    })
    await settle()

    expect(wrapper.text()).toContain('General Prompt Assistant')
    expect(wrapper.text()).toContain('Seeded from the prompt-assistant agent pack.')
    expect(wrapper.text()).toContain('Creative Thinking Defense Prompt Assistant')
    expect(wrapper.text()).toContain('Inactive')
    expect(wrapper.text()).toContain('Active')
    expect(wrapper.text()).toContain('New preset')
  })

  it('shows an empty state with no presets', async () => {
    server.use(http.get('/api/admin/prompt-assistant/presets', () => HttpResponse.json([])))

    const wrapper = await renderView(AdminPromptPresetListView, {
      routes,
      initialRoute: '/admin/prompt-assistant/presets',
    })
    await settle()

    expect(wrapper.text()).toContain('No presets yet')
  })
})
