// Live Knowledge Map build-state subscriber — mirrors useGraphragSocket.test.ts,
// since both share the useBuildStateSocket engine. Had no test coverage at all
// before this (code review finding: reuse/simplification angle).

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

const getConfigMock = vi.hoisted(() =>
  vi.fn(async () => ({ last_build_state: 'idle' })),
)
vi.mock('../api', () => ({
  agentsApi: { getKnowmapConfig: getConfigMock },
  GRAPHRAG_IN_PROGRESS: new Set(['running', 'neo4j_committed', 'failed_compensating']),
}))

import { useKnowmapSocket } from '../composables/useKnowmapSocket'

function emit(ev: Record<string, unknown>): void {
  for (const h of [...subscribedHandlers]) h(ev as ChannelEvent)
}

afterEach(() => {
  subscribedHandlers.length = 0
  statusHandlers.length = 0
  vi.clearAllMocks()
})

function mountSocket(): ReturnType<typeof useKnowmapSocket> {
  return mountSocketWithClient().api
}

function mountSocketWithClient(): { api: ReturnType<typeof useKnowmapSocket>; qc: QueryClient } {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  let api!: ReturnType<typeof useKnowmapSocket>
  const Host = defineComponent({
    setup() {
      api = useKnowmapSocket('proj_1')
      return () => null
    },
  })
  mount(Host, { global: { plugins: [[VueQueryPlugin, { queryClient: qc }]] } })
  return { api, qc }
}

describe('useKnowmapSocket', () => {
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

  it('seeds the initial state so the poll fallback works without a connect', () => {
    const api = mountSocket()
    api.watch('cfg_1', 'running')
    expect(api.liveState.value['cfg_1']).toBe('running')
  })

  it('backstop status sync reads last_build_state off the config row', async () => {
    const api = mountSocket()
    api.watch('cfg_1')

    // Simulate the connect callback firing the backstop sync.
    for (const h of [...statusHandlers]) h(true)
    await Promise.resolve()
    await Promise.resolve()

    expect(getConfigMock).toHaveBeenCalledWith('cfg_1')
    expect(api.liveState.value['cfg_1']).toBe('idle')
  })

  it('invalidates only the knowmap config queries on a terminal build state', () => {
    // Unlike GraphRAG, Knowledge Map has no conceptMapCoverage badge to
    // invalidate — only its own list/single-config queries.
    const { api, qc } = mountSocketWithClient()
    const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')
    api.watch('cfg_1', 'running')

    emit({ type: 'build.state', state: 'qdrant_committed' })

    const invalidatedKeys = invalidateSpy.mock.calls.map((c) => (c[0] as { queryKey: unknown[] }).queryKey)
    expect(invalidatedKeys).toContainEqual(['agents', 'knowmapConfigs', 'proj_1'])
    expect(invalidatedKeys).toContainEqual(['agents', 'knowmapConfig', 'cfg_1'])
    expect(invalidatedKeys).not.toContainEqual(['agents', 'conceptMapCoverage'])
  })
})
