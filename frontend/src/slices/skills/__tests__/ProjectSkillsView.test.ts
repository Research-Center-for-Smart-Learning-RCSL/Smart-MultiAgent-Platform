import { beforeAll, describe, expect, it } from 'vitest'

import { renderView } from '../../../../tests/utils'
import ProjectSkillsView from '../views/ProjectSkillsView.vue'
import { installMessages, seedEmptyList, settle } from './kit'

const routes = [
  { path: '/projects/:projectId/skills', name: 'skills.project', component: ProjectSkillsView },
]

describe('ProjectSkillsView', () => {
  beforeAll(installMessages)

  it('renders the project-scoped workbench for the route project', async () => {
    seedEmptyList('/projects/p_1')
    const wrapper = await renderView(ProjectSkillsView, {
      routes,
      initialRoute: '/projects/p_1/skills',
    })
    await settle()
    expect(wrapper.text()).toContain('Project skills')
    expect(wrapper.text()).toContain('No skills in this scope yet.')
  })
})
