import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ProjectKeysView from '../views/ProjectKeysView.vue'

const routes = [
  { path: '/projects/:projectId/keys', name: 'keys.projectKeys', component: ProjectKeysView },
]

describe('ProjectKeysView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(ProjectKeysView, {
      routes,
      initialRoute: '/projects/proj_1/keys',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('renders page header and tabs', async () => {
    const wrapper = await renderView(ProjectKeysView, {
      routes,
      initialRoute: '/projects/proj_1/keys',
    })
    const text = wrapper.text()
    expect(text).toContain('keys.project.title')
    expect(text).toContain('keys.project.carried')
    expect(text).toContain('keys.project.carry')
  })
})
