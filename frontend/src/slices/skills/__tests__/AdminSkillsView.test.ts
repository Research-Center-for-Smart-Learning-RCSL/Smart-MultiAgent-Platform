import { beforeAll, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'

import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AdminSkillsView from '../views/AdminSkillsView.vue'
import { installMessages, seedEmptyList, settle } from './kit'

describe('AdminSkillsView', () => {
  beforeAll(installMessages)

  it('renders per-scope metrics and the platform workbench', async () => {
    seedEmptyList('/admin')
    server.use(
      http.get('/api/admin/skills/metrics', () =>
        HttpResponse.json({ counts: { agent: 2, project: 5, org: 1, platform: 0 }, total: 8 }),
      ),
    )
    const wrapper = await renderView(AdminSkillsView)
    await settle()
    expect(wrapper.text()).toContain('Platform skills')
    // R31.11 / AC-15: the per-scope ratio is observable.
    expect(wrapper.text()).toContain('8 live skills across all scopes.')
  })
})
