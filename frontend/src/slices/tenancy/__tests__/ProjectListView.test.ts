import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ProjectListView from '../views/ProjectListView.vue'

const routes = [
  { path: '/projects', name: 'tenancy.projectList', component: ProjectListView },
  { path: '/projects/:id', name: 'tenancy.projectDetail', component: { template: '<div />' } },
  { path: '/orgs', name: 'tenancy.orgList', component: { template: '<div />' } },
]

describe('ProjectListView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(ProjectListView, {
      routes,
      initialRoute: '/projects',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('shows tabs and a create button', async () => {
    const wrapper = await renderView(ProjectListView, {
      routes,
      initialRoute: '/projects',
    })
    expect(wrapper.text()).toContain('All')
    expect(wrapper.find('button').exists()).toBe(true)
  })
})
