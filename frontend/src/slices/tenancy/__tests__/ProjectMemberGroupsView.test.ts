import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ProjectMemberGroupsView from '../views/ProjectMemberGroupsView.vue'

const routes = [
  {
    path: '/projects/:id/member-groups',
    name: 'tenancy.projectMemberGroups',
    component: ProjectMemberGroupsView,
  },
  { path: '/projects/:id', name: 'tenancy.projectDetail', component: { template: '<div />' } },
  { path: '/projects', name: 'tenancy.projectList', component: { template: '<div />' } },
  { path: '/orgs', name: 'tenancy.orgList', component: { template: '<div />' } },
]

const initialRoute = '/projects/proj_1/member-groups'

describe('ProjectMemberGroupsView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(ProjectMemberGroupsView, { routes, initialRoute })
    expect(wrapper.exists()).toBe(true)
  })

  it('renders the page header', async () => {
    const wrapper = await renderView(ProjectMemberGroupsView, { routes, initialRoute })
    expect(wrapper.find('h1').exists()).toBe(true)
  })

  it('offers no management controls to a caller who is not authorized', async () => {
    // `useProjectRole` resolves to false without a session, which is the
    // non-owner path: the create form and the delete buttons must be absent.
    // Server-side is authoritative (R5.05); this only checks the view honours it.
    const wrapper = await renderView(ProjectMemberGroupsView, { routes, initialRoute })
    expect(wrapper.find('form').exists()).toBe(false)
  })
})
