// Live GraphRAG build-state subscriber. The transport layer is mocked and
// events are injected via the wildcard handler the composable registers.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { defineComponent } from 'vue'

import type { ChannelEvent } from '@shared/transport'

const subscribedHandlers: Array<(ev: ChannelEvent) => void> = []
const statusHandlers: Array<(connected: boolean) => void> = []

vi.mock('@shared/transport', () => {
  const channel = {
    subscribe: (_name: string, handler: (ev: ChannelEvent) => void) => {
      subscribedHandlers.push(handler)
      return () => {}
    },
    onStatus: (handler: (connected: boolean) => void) => {
      statusHandlers.push(handler)
      return () => {}
    },
    connect: () => {},
    disconnect: () => {},
    close: () => {},
  }
  return { wsManager: { channel: () => channel, close: () => {} } }
})

const getStatusMock = vi.hoisted(() => vi.fn(async () => ({ data: { state: 'idle' } })))
vi.mock('../api', () => ({ agentsApi: { getGraphragStatus: getStatusMock } }))

import { useGraphragSocket } from '../composables/useGraphragSocket'

function emit(ev: Record<string, unknown>): void {
  for (const h of [...subscribedHandlers]) h(ev as ChannelEvent)
}

afterEach(() => {
  subscribedHandlers.length = 0
  statusHandlers.length = 0
  vi.clearAllMocks()
})

function mountSocket(): ReturnType<typeof useGraphragSocket> {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  let api!: ReturnType<typeof useGraphragSocket>
  const Host = defineComponent({
    setup() {
      api = useGraphragSocket('proj_1')
      return () => null
    },
  })
  mount(Host, { global: { plugins: [[VueQueryPlugin, { queryClient: qc }]] } })
  return api
}

describe('useGraphragSocket', () => {
  it('tracks build.state transitions for a watched config', () => {
    const api = mountSocket()
    api.watch('cfg_1')

    emit({ type: 'build.state', state: 'running' })
    expect(api.liveState.value['cfg_1']).toBe('running')

    emit({ type: 'build.state', state: 'neo4j_committed' })
    expect(api.liveState.value['cfg_1']).toBe('neo4j_committed')

    emit({ type: 'build.state', state: 'idle' })
    expect(api.liveState.value['cfg_1']).toBe('idle')
  })

  it('ignores events that are not build.state', () => {
    const api = mountSocket()
    api.watch('cfg_1')
    emit({ type: 'something.else', state: 'running' })
    expect(api.liveState.value['cfg_1']).toBeUndefined()
  })
})
