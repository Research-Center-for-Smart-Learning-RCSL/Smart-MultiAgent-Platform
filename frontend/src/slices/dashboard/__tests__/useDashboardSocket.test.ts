import { afterEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h, ref } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'

const socketHarness = vi.hoisted(() => ({
  closeHandlers: [] as Array<(code: number, reason: string) => void>,
  degradedHandlers: [] as Array<(degraded: boolean) => void>,
  eventHandlers: [] as Array<(event: { type: string; payload?: Record<string, unknown> }) => void>,
  disconnect: vi.fn(),
  connect: vi.fn(),
  invalidateQueries: vi.fn(() => Promise.resolve()),
  closeCodeRegistered: vi.fn(),
  workspaceError: null as Error | null,
  refetchWorkspace: vi.fn(() => Promise.resolve()),
}))

vi.mock('@tanstack/vue-query', () => ({
  useQuery: () => ({
    data: { value: { project_id: 'workspace-project', name: 'Classroom' } },
    error: { value: socketHarness.workspaceError },
    refetch: socketHarness.refetchWorkspace,
  }),
  useQueryClient: () => ({ invalidateQueries: socketHarness.invalidateQueries }),
}))

vi.mock('@shared/transport/ws-manager', () => ({
  wsManager: {
    channel: () => ({
      disconnect: socketHarness.disconnect,
      connect: socketHarness.connect,
      subscribe: vi.fn((_eventName: string, handler: (event: { type: string; payload?: Record<string, unknown> }) => void) => {
        socketHarness.eventHandlers.push(handler)
        return vi.fn()
      }),
      onStatus: vi.fn(() => vi.fn()),
      onDegraded: vi.fn((handler: (degraded: boolean) => void) => {
        socketHarness.degradedHandlers.push(handler)
        handler(false)
        return vi.fn()
      }),
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

function mountSocket() {
  let result: ReturnType<typeof useDashboardSocket> | undefined
  const Harness = defineComponent({
    setup() {
      result = useDashboardSocket(
        () => 'w1',
        ref(new Set(['room-1'])),
        () => ['dashboard', 'timeseries', 'w1', '1h', '5m'],
      )
      return () => h('div')
    },
  })
  const wrapper = mount(Harness)
  if (!result) throw new Error('Socket composable did not initialize')
  return { result, wrapper }
}

describe('useDashboardSocket', () => {
  afterEach(() => {
    socketHarness.closeHandlers = []
    socketHarness.degradedHandlers = []
    socketHarness.eventHandlers = []
    socketHarness.workspaceError = null
    vi.clearAllMocks()
    vi.useRealTimers()
  })

  it.each([4403, 4404])('stops reconnecting on project close code %s', (code) => {
    const { result, wrapper } = mountSocket()
    expect(result.projectId.value).toBe('workspace-project')
    expect(result.workspace.value?.name).toBe('Classroom')
    expect(socketHarness.closeCodeRegistered).toHaveBeenCalledOnce()

    socketHarness.closeHandlers[0]?.(code, 'access denied')

    expect(socketHarness.disconnect).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('exposes a retry action when workspace metadata fails', async () => {
    socketHarness.workspaceError = new Error('Workspace metadata unavailable')
    const { result, wrapper } = mountSocket()

    expect(result.workspaceError.value?.message).toBe('Workspace metadata unavailable')
    await result.refetchWorkspace()

    expect(socketHarness.refetchWorkspace).toHaveBeenCalledOnce()
    wrapper.unmount()
  })

  it('invalidates dashboard queries for a room-independent project event', async () => {
    vi.useFakeTimers()
    const { wrapper } = mountSocket()

    socketHarness.eventHandlers[0]?.({
      type: 'dashboard.invalidated',
      payload: {},
    })
    await vi.advanceTimersByTimeAsync(500)

    expect(socketHarness.invalidateQueries).toHaveBeenCalledOnce()
    expect(socketHarness.invalidateQueries).toHaveBeenCalledWith({
      queryKey: ['dashboard'],
    })
    wrapper.unmount()
  })

  it('invalidates all dashboard queries after degraded connection recovery', async () => {
    const { wrapper } = mountSocket()
    socketHarness.degradedHandlers[0]?.(true)
    socketHarness.degradedHandlers[0]?.(false)
    await flushPromises()

    expect(socketHarness.invalidateQueries).toHaveBeenCalledOnce()
    expect(socketHarness.invalidateQueries).toHaveBeenCalledWith({ queryKey: ['dashboard'] })
    wrapper.unmount()
  })
})
