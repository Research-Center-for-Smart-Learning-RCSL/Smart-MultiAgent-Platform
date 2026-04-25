import { describe, it, expect, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'

// Stub the underlying API modules so the composables produce a non-empty
// `detail.value`/`carried.value` synchronously, without needing a live HTTP
// transport. Mirrors backend `/api/key-groups/{id}` and
// `/api/projects/{}/keys` shapes.
vi.mock('../api/key-groups', () => ({
  keyGroupsApi: {
    get: vi.fn(async (_groupId: string) => ({
      data: {
        group: {
          id: 'kg_1',
          project_id: 'proj_1',
          name: 'Group One',
          created_at: new Date().toISOString(),
        },
        members: [],
      },
    })),
    addMember: vi.fn(async () => ({ data: {} })),
    removeMember: vi.fn(async () => ({ data: {} })),
    patchMember: vi.fn(async () => ({ data: {} })),
    reorder: vi.fn(async () => ({ data: {} })),
  },
}))

vi.mock('../api/project-keys', () => ({
  projectKeysApi: {
    listCarried: vi.fn(async (_projectId: string) => ({ data: [] })),
  },
}))

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
    await flushPromises()
    expect(wrapper.find('[data-testid="add-member-select"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="add-member"]').exists()).toBe(true)
  })
})
