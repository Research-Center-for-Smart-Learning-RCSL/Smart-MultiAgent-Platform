// Live GraphRAG build-state subscriber. The transport layer is mocked and
// events are injected via the wildcard handler the composable registers.

import { afterEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
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

const getStatusMock = vi.hoisted(() => vi.fn(async () => ({ state: 'idle' })))
vi.mock('../api', () => ({
  agentsApi: { getGraphragStatus: getStatusMock },
  GRAPHRAG_IN_PROGRESS: new Set(['running', 'neo4j_committed', 'failed_compensating']),
}))

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
  return mountSocketWithClient().api
}

function mountSocketWithClient(): { api: ReturnType<typeof useGraphragSocket>; qc: QueryClient } {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  let api!: ReturnType<typeof useGraphragSocket>
  const Host = defineComponent({
    setup() {
      api = useGraphragSocket('proj_1')
      return () => null
    },
  })
  mount(Host, { global: { plugins: [[VueQueryPlugin, { queryClient: qc }]] } })
  return { api, qc }
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

  it('seeds the initial state so the poll fallback works without a connect (C6)', () => {
    const api = mountSocket()
    // No WS event and no successful connect — only the seed.
    api.watch('cfg_1', 'running')
    expect(api.liveState.value['cfg_1']).toBe('running')
  })

  it('drops a stale resync that resolves after a terminal build.state frame (B1)', async () => {
    // A reconnect issues a status resync; before it resolves, a terminal 'idle'
    // frame arrives and closes the channel. The in-flight resync then resolves
    // with the pre-terminal 'running'. Without a generation guard it would
    // overwrite the terminal state and strand the row as in-progress with no
    // live channel; the guard must drop the stale result.
    let resolveStatus!: (v: { state: string }) => void
    getStatusMock.mockImplementation(
      () => new Promise<{ state: string }>((resolve) => {
        resolveStatus = resolve
      }),
    )
    const api = mountSocket()
    api.watch('cfg_1', 'running')
    // Reconnect fires the status handler, issuing syncStatus (fetchStatus pending).
    for (const h of [...statusHandlers]) h(true)

    // Terminal frame lands while the resync is in flight.
    emit({ type: 'build.state', state: 'idle' })
    expect(api.liveState.value['cfg_1']).toBe('idle')

    // The stale resync now resolves with the pre-terminal running state.
    resolveStatus({ state: 'running' })
    await flushPromises()

    expect(api.liveState.value['cfg_1']).toBe('idle')
  })

  it('invalidates conceptMapCoverage (not just the config queries) on a terminal build state', () => {
    // Regression: the agent Knowledge tab's read-only coverage badge
    // (agentKeys.conceptMapCoverage) went stale after a covering map's build
    // finished, because only graphragConfigs/graphragConfig were invalidated —
    // the composable has no way to know which agent(s) a config covers, so it
    // must invalidate the whole conceptMapCoverage prefix.
    const { api, qc } = mountSocketWithClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')
    api.watch('cfg_1', 'running')

    emit({ type: 'build.state', state: 'qdrant_committed' })

    const invalidatedKeys = invalidateSpy.mock.calls.map((c) => (c[0] as { queryKey: unknown[] }).queryKey)
    expect(invalidatedKeys).toContainEqual(['agents', 'graphragConfigs', 'proj_1'])
    expect(invalidatedKeys).toContainEqual(['agents', 'graphragConfig', 'cfg_1'])
    expect(invalidatedKeys).toContainEqual(['agents', 'conceptMapCoverage'])
  })
})
