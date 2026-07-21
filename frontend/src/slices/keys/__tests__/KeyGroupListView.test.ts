import { describe, it, expect, vi } from 'vitest'
import { renderView } from '../../../../tests/utils'
import KeyGroupListView from '../views/KeyGroupListView.vue'

const routes = [
  { path: '/projects/:projectId/key-groups', name: 'keys.groupList', component: KeyGroupListView },
  { path: '/projects/:projectId/key-groups/:id', name: 'keys.groupDetail', component: { template: '<div />' } },
]

describe('KeyGroupListView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(KeyGroupListView, {
      routes,
      initialRoute: '/projects/proj_1/key-groups',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('renders page header and create button', async () => {
    const wrapper = await renderView(KeyGroupListView, {
      routes,
      initialRoute: '/projects/proj_1/key-groups',
    })
    const text = wrapper.text()
    expect(text).toContain('keys.groups.listTitle')
    expect(text).toContain('keys.groups.create')
  })

  // Regression: STable defaults `loading` to false, so a view that binds only
  // :data renders the empty state while the query is still in flight. That
  // false "no groups yet" is what led 03-llm-key-flow to create a spurious
  // group, which then sorted ahead of the seeded one (list is created_at DESC)
  // and got auto-selected by the agent form, 422-ing the agent create.
  it('shows the loading skeleton, not the empty state, while groups load', async () => {
    const wrapper = await renderView(KeyGroupListView, {
      routes,
      initialRoute: '/projects/proj_1/key-groups',
    })

    expect(wrapper.find('.s-table__skeleton').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('keys.groups.emptyTitle')
  })

  it('renders the loaded groups once the query settles', async () => {
    const wrapper = await renderView(KeyGroupListView, {
      routes,
      initialRoute: '/projects/proj_1/key-groups',
    })

    await vi.waitFor(() => {
      expect(wrapper.text()).toContain('Default Group')
    })
    expect(wrapper.find('.s-table__skeleton').exists()).toBe(false)
  })
})
