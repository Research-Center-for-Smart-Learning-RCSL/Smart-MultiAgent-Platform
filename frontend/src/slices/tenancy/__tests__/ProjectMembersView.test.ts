import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ProjectMembersView from '../views/ProjectMembersView.vue'

const routes = [
  { path: '/projects/:id/members', name: 'tenancy.projectMembers', component: ProjectMembersView },
  { path: '/projects/:id', name: 'tenancy.projectDetail', component: { template: '<div />' } },
  { path: '/projects', name: 'tenancy.projectList', component: { template: '<div />' } },
  { path: '/orgs', name: 'tenancy.orgList', component: { template: '<div />' } },
]

describe('ProjectMembersView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(ProjectMembersView, {
      routes,
      initialRoute: '/projects/proj_1/members',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('renders the page header', async () => {
    const wrapper = await renderView(ProjectMembersView, {
      routes,
      initialRoute: '/projects/proj_1/members',
    })
    expect(wrapper.find('h1').exists()).toBe(true)
  })
})
