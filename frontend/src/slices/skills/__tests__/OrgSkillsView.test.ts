import { beforeAll, describe, expect, it } from 'vitest'

import { renderView } from '../../../../tests/utils'
import OrgSkillsView from '../views/OrgSkillsView.vue'
import { installMessages, seedEmptyList, settle } from './kit'

const routes = [{ path: '/orgs/:orgId/skills', name: 'skills.org', component: OrgSkillsView }]

describe('OrgSkillsView', () => {
  beforeAll(installMessages)

  it('renders the org-scoped workbench for the route org', async () => {
    seedEmptyList('/orgs/o_1')
    const wrapper = await renderView(OrgSkillsView, {
      routes,
      initialRoute: '/orgs/o_1/skills',
    })
    await settle()
    expect(wrapper.text()).toContain('Organization skills')
  })
})
