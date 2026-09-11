import { describe, it, expect, vi } from 'vitest'
import { ref } from 'vue'

vi.mock('@shared/query-client', () => ({
  queryClient: {
    invalidateQueries: vi.fn(),
    defaultQueryOptions: vi.fn(() => ({})),
    getDefaultOptions: vi.fn(() => ({ queries: {} })),
  },
}))

vi.mock('@shared/transport/axios', () => ({
  http: {
    get: vi.fn(),
    post: vi.fn(),
    patch: vi.fn(),
    delete: vi.fn(),
  },
}))

vi.mock('@shared/composables/useConfirmDialog', () => ({
  useConfirmDialog: () => ({ confirm: vi.fn(async () => true) }),
}))

vi.mock('@shared/composables/useToast', () => ({
  useToast: () => ({ success: vi.fn() }),
}))

const mockSnapshots = [
  {
    id: 'snap-1',
    canvas_id: 'canvas-1',
    agent_digest: '2 notes, 1 shape',
    created_by_user_id: 'user-1',
    label: 'My checkpoint',
    created_at: '2026-09-10T12:00:00Z',
  },
  {
    id: 'snap-2',
    canvas_id: 'canvas-1',
    agent_digest: '1 note',
    created_by_user_id: null,
    label: null,
    created_at: '2026-09-10T11:00:00Z',
  },
]

vi.mock('@tanstack/vue-query', () => ({
  useQuery: vi.fn(() => ({
    data: ref(mockSnapshots),
    isLoading: ref(false),
    error: ref(null),
  })),
  useQueryClient: vi.fn(() => ({
    invalidateQueries: vi.fn(),
  })),
  useMutation: vi.fn(() => ({
    mutateAsync: vi.fn(),
    isPending: ref(false),
  })),
}))

import { mount } from '@vue/test-utils'
import CanvasHistory from '../components/CanvasHistory.vue'

describe('CanvasHistory', () => {
  it('renders snapshot list with labels and digests', () => {
    const wrapper = mount(CanvasHistory, {
      props: { chatroomId: 'room-1' },
      global: {
        stubs: {
          SRelativeTime: { template: '<span>time</span>' },
          SLoadingSpinner: { template: '<span>loading</span>' },
        },
        mocks: {
          $t: (key: string) => key,
        },
      },
    })

    expect(wrapper.text()).toContain('My checkpoint')
    expect(wrapper.text()).toContain('2 notes, 1 shape')
    expect(wrapper.text()).toContain('1 note')
  })

  it('emits close when close button is clicked', async () => {
    const wrapper = mount(CanvasHistory, {
      props: { chatroomId: 'room-1' },
      global: {
        stubs: {
          SRelativeTime: { template: '<span>time</span>' },
          SLoadingSpinner: { template: '<span>loading</span>' },
        },
        mocks: {
          $t: (key: string) => key,
        },
      },
    })

    await wrapper.find('.canvas-history__close').trigger('click')
    expect(wrapper.emitted('close')).toBeTruthy()
  })
})
