import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ProjectListView from '../views/ProjectListView.vue'

const routes = [
  { path: '/projects', name: 'tenancy.projectList', component: ProjectListView },
  { path: '/projects/:id', name: 'tenancy.projectDetail', component: { template: '<div />' } },
]

describe('ProjectListView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(ProjectListView, {
      routes,
      initialRoute: '/projects?scope=user&id=u_test',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('has a scope selector and create form', async () => {
    const wrapper = await renderView(ProjectListView, {
      routes,
      initialRoute: '/projects?scope=user&id=u_test',
    })
    expect(wrapper.find('select').exists()).toBe(true)
    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.find('button[type="submit"]').exists()).toBe(true)
  })
})
