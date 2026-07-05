import { beforeAll, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'

import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import PromptTemplatePicker from '../components/PromptTemplatePicker.vue'
import { installMessages, settle } from './kit'

function tpl(id: string, scope: string, name: string): Record<string, unknown> {
  return {
    id,
    scope,
    name,
    description: `${name} desc`,
    body: `${name} body`,
    position: 0,
    version: 1,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
}

describe('PromptTemplatePicker', () => {
  beforeAll(installMessages)

  it('groups templates by scope and emits the body on insert', async () => {
    server.use(
      http.get('/api/projects/proj_1/prompt-templates', () =>
        HttpResponse.json([tpl('t1', 'platform', 'Platform One'), tpl('t2', 'user', 'Personal One')]),
      ),
    )
    const wrapper = await renderView(PromptTemplatePicker, {
      props: { projectId: 'proj_1' },
    })
    await settle()

    // Open the modal.
    await wrapper.find('button').trigger('click')
    await settle()

    expect(wrapper.text()).toContain('Platform One')
    expect(wrapper.text()).toContain('Personal One')

    // Insert the first template.
    const insertButtons = wrapper.findAll('button').filter((b) => b.text() === 'Insert')
    expect(insertButtons.length).toBe(2)
    await insertButtons[0]!.trigger('click')

    expect(wrapper.emitted('insert')?.[0]).toEqual(['Platform One body'])
  })
})
