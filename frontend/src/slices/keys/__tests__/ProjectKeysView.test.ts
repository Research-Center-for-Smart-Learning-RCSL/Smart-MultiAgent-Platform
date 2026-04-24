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

  it('shows carried and carriable key lists', async () => {
    const wrapper = await renderView(ProjectKeysView, {
      routes,
      initialRoute: '/projects/proj_1/keys',
    })
    await new Promise((r) => setTimeout(r, 50))
    await wrapper.vm.$nextTick()
    expect(wrapper.find('[data-testid="carried-list"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="carriable-list"]').exists()).toBe(true)
  })
})
