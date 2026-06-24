import { describe, it, expect } from 'vitest'
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
})
