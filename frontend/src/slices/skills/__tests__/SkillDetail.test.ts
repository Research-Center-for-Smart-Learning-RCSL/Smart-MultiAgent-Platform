import { beforeAll, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'

import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import SkillDetail from '../components/SkillDetail.vue'
import { installMessages, settle } from './kit'

const skill = {
  id: 's_1',
  scope: 'project',
  owner_id: 'p_1',
  name: 'pdf-fill',
  description: 'Fills a PDF form.',
  body: '# how to fill',
  body_sha256: 'abc',
  source: 'authored',
  bundle_sha256: null,
  diverged: false,
  requires: [],
  allowed_tools: ['web_search'],
  created_by: 'u_1',
  version: 1,
  created_at: 't',
  deleted_at: null,
}

describe('SkillDetail', () => {
  beforeAll(installMessages)

  it('shows the not-enforced label beside allowed-tools (AC-22)', async () => {
    server.use(
      http.get('/api/projects/:pid/skills/:sid', () => HttpResponse.json(skill)),
      http.get('/api/projects/:pid/skills/:sid/files', () => HttpResponse.json([])),
    )
    const wrapper = await renderView(SkillDetail, {
      props: { scope: { kind: 'project', projectId: 'p_1' }, skillId: 's_1' },
    })
    await settle()
    expect(wrapper.text()).toContain('pdf-fill')
    expect(wrapper.text()).toContain('not enforced by SMAP')
  })
})
