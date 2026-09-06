import { http, HttpResponse } from 'msw'
import { beforeAll, describe, expect, it } from 'vitest'

import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AdminPromptPresetEditView from '../views/AdminPromptPresetEditView.vue'
import { installMessages, settle } from './kit'

const routes = [
  {
    path: '/admin/prompt-assistant/presets/new',
    name: 'admin.promptPresetNew',
    component: AdminPromptPresetEditView,
  },
  {
    path: '/admin/prompt-assistant/presets/:presetId',
    name: 'admin.promptPresetEdit',
    component: AdminPromptPresetEditView,
  },
]

const preset = {
  id: 'cfg_1',
  scope: 'platform',
  name: 'General Prompt Assistant',
  description: 'Seeded from the prompt-assistant agent pack.',
  enabled: false,
  persona_prompt: 'You are a prompt-engineering assistant.',
  system_prompt: 'Extra guidance.',
  key_id: null,
  key: null,
  key_revoked: false,
  model_id: null,
  daily_request_limit_per_user: 50,
  hide_platform_templates: false,
  version: 1,
  files: [],
}

function seedCommon(): void {
  server.use(
    http.get('/api/keys', () => HttpResponse.json([])),
    http.get('/api/model-catalog', () => HttpResponse.json({ chat: [] })),
  )
}

describe('AdminPromptPresetEditView', () => {
  beforeAll(installMessages)

  it('loads an existing preset into the form, including name/description/persona (AC-9)', async () => {
    seedCommon()
    server.use(http.get('/api/admin/prompt-assistant/presets', () => HttpResponse.json([preset])))

    const wrapper = await renderView(AdminPromptPresetEditView, {
      routes,
      initialRoute: '/admin/prompt-assistant/presets/cfg_1',
    })
    await settle()

    const html = wrapper.html()
    expect(html).toContain('General Prompt Assistant')
    expect(html).toContain('Seeded from the prompt-assistant agent pack.')
    expect(html).toContain('You are a prompt-engineering assistant.')
    expect(html).toContain('Extra guidance.')
  })

  it('shows a blank form in create mode', async () => {
    seedCommon()
    server.use(http.get('/api/admin/prompt-assistant/presets', () => HttpResponse.json([])))

    const wrapper = await renderView(AdminPromptPresetEditView, {
      routes,
      initialRoute: '/admin/prompt-assistant/presets/new',
    })
    await settle()

    expect(wrapper.text()).toContain('New preset')
    expect(wrapper.html()).not.toContain('General Prompt Assistant')
  })

  it('shows a not-found message for an unknown preset id once the list has loaded', async () => {
    seedCommon()
    server.use(http.get('/api/admin/prompt-assistant/presets', () => HttpResponse.json([preset])))

    const wrapper = await renderView(AdminPromptPresetEditView, {
      routes,
      initialRoute: '/admin/prompt-assistant/presets/does-not-exist',
    })
    await settle()

    expect(wrapper.text()).toContain('This preset no longer exists.')
  })
})
