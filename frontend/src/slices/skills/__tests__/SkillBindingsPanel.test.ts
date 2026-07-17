import { beforeAll, describe, expect, it } from 'vitest'
import { http, HttpResponse } from 'msw'

import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import SkillBindingsPanel from '../components/SkillBindingsPanel.vue'
import { installMessages, settle } from './kit'

function candidate(id: string, name: string, scope: string) {
  return {
    id,
    scope,
    owner_id: 'x',
    name,
    description: `${name} desc`,
    body_sha256: 'h',
    source: 'authored',
    bundle_sha256: null,
    diverged: false,
    requires: [],
    allowed_tools: [],
    created_by: 'u',
    version: 1,
    created_at: 't',
    deleted_at: null,
  }
}

describe('SkillBindingsPanel', () => {
  beforeAll(installMessages)

  it('lists bound skills and bindable candidates from agent + project scope', async () => {
    server.use(
      http.get('/api/agents/:aid/skill-bindings', () =>
        HttpResponse.json([{ agent_id: 'a_1', skill_id: 's_bound', name: 'bound-skill' }]),
      ),
      http.get('/api/agents/:aid/skills', () =>
        HttpResponse.json({ items: [candidate('s_a', 'agent-skill', 'agent')], total: 1 }),
      ),
      http.get('/api/projects/:pid/skills', () =>
        HttpResponse.json({ items: [candidate('s_p', 'proj-skill', 'project')], total: 1 }),
      ),
    )
    const wrapper = await renderView(SkillBindingsPanel, {
      props: { agentId: 'a_1', projectId: 'p_1' },
    })
    await settle()
    // Bound skill with an Unbind action.
    expect(wrapper.text()).toContain('bound-skill')
    expect(wrapper.text()).toContain('Unbind')
    // Candidates from both agent and project scope, each with a Bind action.
    expect(wrapper.text()).toContain('agent-skill')
    expect(wrapper.text()).toContain('proj-skill')
    expect(wrapper.text()).toContain('Bind')
  })
})
