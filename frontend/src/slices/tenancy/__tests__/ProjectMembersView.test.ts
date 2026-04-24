import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ProjectMembersView from '../views/ProjectMembersView.vue'

const routes = [
  { path: '/projects/:id/members', name: 'tenancy.projectMembers', component: ProjectMembersView },
]

describe('ProjectMembersView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(ProjectMembersView, {
      routes,
      initialRoute: '/projects/proj_1/members',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('contains an invite form with email input and role select', async () => {
    const wrapper = await renderView(ProjectMembersView, {
      routes,
      initialRoute: '/projects/proj_1/members',
    })
    expect(wrapper.find('input[type="email"]').exists()).toBe(true)
    expect(wrapper.find('select').exists()).toBe(true)
    expect(wrapper.find('button[type="submit"]').exists()).toBe(true)
  })
})
