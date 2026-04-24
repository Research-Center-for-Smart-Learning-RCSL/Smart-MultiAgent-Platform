import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'
import ProjectDetailView from '../views/ProjectDetailView.vue'

const routes = [
  { path: '/projects/:id', name: 'tenancy.projectDetail', component: ProjectDetailView },
  { path: '/projects', name: 'tenancy.projectList', component: { template: '<div />' } },
  { path: '/projects/:id/members', name: 'tenancy.projectMembers', component: { template: '<div />' } },
]

describe('ProjectDetailView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(ProjectDetailView, {
      routes,
      initialRoute: '/projects/proj_1',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('displays project name after loading', async () => {
    const wrapper = await renderView(ProjectDetailView, {
      routes,
      initialRoute: '/projects/proj_1',
    })
    await flushPromises()
    expect(wrapper.text()).toContain('Test Project')
  })
})
