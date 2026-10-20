import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { mount } from '@vue/test-utils'

const socketHarness = vi.hoisted(() => ({
  closeHandlers: [] as Array<(code: number, reason: string) => void>,
  disconnect: vi.fn(),
  connect: vi.fn(),
  subscribe: vi.fn(() => vi.fn()),
  onStatus: vi.fn(() => vi.fn()),
  closeCodeRegistered: vi.fn(),
}))

vi.mock('@tanstack/vue-query', () => ({
  useQuery: () => ({ data: { value: { project_id: 'workspace-project' } } }),
  useQueryClient: () => ({ invalidateQueries: vi.fn() }),
}))

vi.mock('@shared/transport/ws-manager', () => ({
  wsManager: {
    channel: () => ({
      ...socketHarness,
      onCloseCode: vi.fn((handler: (code: number, reason: string) => void) => {
        socketHarness.closeHandlers.push(handler)
        socketHarness.closeCodeRegistered()
        return vi.fn()
      }),
    }),
    close: vi.fn(),
  },
}))

import { useDashboardSocket } from '../composables/useDashboardSocket'

describe('useDashboardSocket', () => {
  afterEach(() => {
    socketHarness.closeHandlers = []
    vi.clearAllMocks()
  })

  it.each([4403, 4404])('stops reconnecting on project close code %s', (code) => {
    let resolvedProjectId = ''
    const Harness = defineComponent({
      setup() {
        const result = useDashboardSocket(() => 'w1', ref(new Set<string>()))
        resolvedProjectId = result.projectId.value
        return () => h('div')
      },
    })

    const wrapper = mount(Harness)
    expect(resolvedProjectId).toBe('workspace-project')
    expect(socketHarness.closeCodeRegistered).toHaveBeenCalledOnce()

    socketHarness.closeHandlers[0]?.(code, 'access denied')

    expect(socketHarness.disconnect).toHaveBeenCalledOnce()
    wrapper.unmount()
  })
})
