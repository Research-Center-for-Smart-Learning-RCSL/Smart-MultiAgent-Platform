import { beforeAll, describe, expect, it } from 'vitest'

import { renderView } from '../../../../tests/utils'
import OrgPromptStudioView from '../views/OrgPromptStudioView.vue'
import { installMessages, seedConfigScope, settle } from './kit'

const routes = [
  { path: '/orgs/:orgId/prompt-assistant', name: 'prompt-studio.org', component: OrgPromptStudioView },
]

describe('OrgPromptStudioView', () => {
  beforeAll(installMessages)

  it('renders the org-scoped config for the route org', async () => {
    seedConfigScope('/orgs/org_1/prompt-assistant', '/orgs/org_1/prompt-templates')
    const wrapper = await renderView(OrgPromptStudioView, {
      routes,
      initialRoute: '/orgs/org_1/prompt-assistant',
    })
    await settle()

    expect(wrapper.text()).toContain('Organization Prompt Assistant')
    expect(wrapper.text()).toContain('Assistant configuration')
    // The org scope exposes the platform-template hide toggle.
    expect(wrapper.text()).toContain("Hide platform templates")
  })
})
