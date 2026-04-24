import { describe, it, expect } from 'vitest'
import { renderView } from '../../../../tests/utils'
import WorkspaceListView from '../views/WorkspaceListView.vue'

const routes = [
  {
    path: '/projects/:projectId/workspaces',
    name: 'conversation.workspaces',
    component: WorkspaceListView,
  },
  {
    path: '/workspaces/:workspaceId/chatrooms',
    name: 'conversation.chatrooms',
    component: { template: '<div />' },
  },
]

describe('WorkspaceListView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(WorkspaceListView, {
      routes,
      initialRoute: '/projects/proj_1/workspaces',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('shows the create form with an input and submit button', async () => {
    const wrapper = await renderView(WorkspaceListView, {
      routes,
      initialRoute: '/projects/proj_1/workspaces',
    })
    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.find('input').exists()).toBe(true)
    expect(wrapper.find('button[type="submit"]').exists()).toBe(true)
  })
})
