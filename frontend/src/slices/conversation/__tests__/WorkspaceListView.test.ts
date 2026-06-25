import { describe, it, expect } from 'vitest'
import { nextTick } from 'vue'
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

  it('opens the create modal with a name input', async () => {
    const wrapper = await renderView(WorkspaceListView, {
      routes,
      initialRoute: '/projects/proj_1/workspaces',
    })
    const trigger = wrapper.find('[data-testid="create-workspace"]')
    expect(trigger.exists()).toBe(true)

    await trigger.trigger('click')
    await nextTick()

    // The create form lives in a modal; opening it reveals the name input.
    expect(wrapper.find('form').exists()).toBe(true)
    expect(wrapper.find('form input').exists()).toBe(true)
  })
})
