import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'
import KeyGroupDetailView from '../views/KeyGroupDetailView.vue'

const routes = [
  {
    path: '/projects/:projectId/key-groups/:id',
    name: 'keys.groupDetail',
    component: KeyGroupDetailView,
  },
]

describe('KeyGroupDetailView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(KeyGroupDetailView, {
      routes,
      initialRoute: '/projects/proj_1/key-groups/kg_1',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('shows add-member form controls', async () => {
    const wrapper = await renderView(KeyGroupDetailView, {
      routes,
      initialRoute: '/projects/proj_1/key-groups/kg_1',
    })
    await flushPromises()
    await new Promise(r => setTimeout(r, 100))
    expect(wrapper.find('[data-testid="add-member-select"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="add-member"]').exists()).toBe(true)
  })
})
