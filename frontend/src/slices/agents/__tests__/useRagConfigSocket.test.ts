// F-21 / F-28: the RAG ingestion socket must recover authoritative state on
// (re)connect, mount, and late subscribe by deriving `progress` from the
// per-document REST list, never from the chunk-granular live frames. Mirrors the
// sibling useKnowmapSocket.test.ts / useGraphragSocket.test.ts recovery tests.

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

const listDocumentsMock = vi.hoisted(() => vi.fn(async () => [] as unknown[]))
vi.mock('../api', () => ({
  agentsApi: { listDocuments: listDocumentsMock },
}))

import { useRagConfigSocket } from '../composables/useRagConfigSocket'

interface Doc {
  id: string
  status: 'ingesting' | 'ready' | 'failed' | 'quarantined'
}

function doc(id: string, status: Doc['status']): Doc {
  return { id, status }
}

function emit(ev: Record<string, unknown>): void {
  for (const h of [...subscribedHandlers]) h(ev as ChannelEvent)
}

function fireStatus(connected: boolean): void {
  for (const h of [...statusHandlers]) h(connected)
}

afterEach(() => {
  subscribedHandlers.length = 0
  statusHandlers.length = 0
  vi.clearAllMocks()
  vi.useRealTimers()
})

function mountSocket(): {
  api: ReturnType<typeof useRagConfigSocket>
  qc: QueryClient
  invalidateSpy: ReturnType<typeof vi.spyOn>
} {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const invalidateSpy = vi.spyOn(qc, 'invalidateQueries')
  let api!: ReturnType<typeof useRagConfigSocket>
  const Host = defineComponent({
    setup() {
      api = useRagConfigSocket('cfg_1', 'proj_1')
      return () => null
    },
  })
  mount(Host, { global: { plugins: [[VueQueryPlugin, { queryClient: qc }]] } })
  return { api, qc, invalidateSpy }
}

function invalidatedKeys(spy: ReturnType<typeof vi.spyOn>): unknown[][] {
  return spy.mock.calls.map((c) => (c[0] as { queryKey: unknown[] }).queryKey)
}

describe('useRagConfigSocket', () => {
  it('rebuilds progress to ready on reconnect after a missed terminal event (F-21)', async () => {
    listDocumentsMock.mockResolvedValue([doc('d1', 'ingesting')])
    const { api, invalidateSpy } = mountSocket()
    await flushPromises()
    expect(api.progress.value.state).toBe('ingesting')

    // Ingestion finished while the socket was disconnected; reconnect resyncs.
    listDocumentsMock.mockResolvedValue([doc('d1', 'ready'), doc('d2', 'ready')])
    fireStatus(false)
    fireStatus(true)
    await flushPromises()

    expect(api.progress.value.state).toBe('ready')
    expect(api.progress.value.documentsTotal).toBe(2)
    expect(api.progress.value.documentsProcessed).toBe(2)
    expect(invalidatedKeys(invalidateSpy)).toContainEqual(['agents', 'ragDocuments', 'cfg_1'])
  })

  it('derives ingesting state on a late mid-ingestion subscribe without a started event (F-28)', async () => {
    listDocumentsMock.mockResolvedValue([doc('d1', 'ingesting'), doc('d2', 'ready')])
    const { api } = mountSocket()
    await flushPromises()

    expect(api.progress.value.state).toBe('ingesting')
    expect(api.progress.value.documentsTotal).toBe(2)
    expect(api.progress.value.documentsProcessed).toBe(1)
  })

  it('derives failed state when a document failed and none are ingesting', async () => {
    listDocumentsMock.mockResolvedValue([doc('d1', 'failed'), doc('d2', 'ready')])
    const { api } = mountSocket()
    await flushPromises()

    expect(api.progress.value.state).toBe('failed')
    expect(api.progress.value.error).not.toBeNull()
  })

  it('backstop poll self-heals a dropped terminal frame and then stops', async () => {
    vi.useFakeTimers()
    listDocumentsMock.mockResolvedValue([doc('d1', 'ingesting')])
    const { api } = mountSocket()
    await vi.advanceTimersByTimeAsync(0)
    expect(api.progress.value.state).toBe('ingesting')

    // No terminal event arrives; documents complete server-side.
    listDocumentsMock.mockResolvedValue([doc('d1', 'ready')])
    await vi.advanceTimersByTimeAsync(15000)
    expect(api.progress.value.state).toBe('ready')

    // Poll has stopped: a later document change is not picked up by polling.
    listDocumentsMock.mockClear()
    listDocumentsMock.mockResolvedValue([doc('d1', 'ingesting')])
    await vi.advanceTimersByTimeAsync(30000)
    expect(listDocumentsMock).not.toHaveBeenCalled()
  })

  it('coalesces a burst of per-document frames into a single debounced refetch', async () => {
    vi.useFakeTimers()
    listDocumentsMock.mockResolvedValue([doc('d1', 'ingesting')])
    mountSocket()
    await vi.advanceTimersByTimeAsync(0)
    // Ignore the immediate mount/seed fetch; measure only event-driven refetches.
    listDocumentsMock.mockClear()

    // A multi-file upload sprays per-document frames within one window.
    emit({ type: 'ingestion.started' })
    emit({ type: 'ingestion.progress', processed: 3, total: 10 })
    emit({ type: 'ingestion.completed' })
    emit({ type: 'ingestion.started' })
    emit({ type: 'ingestion.completed' })
    // Before the debounce window elapses, no refetch has fired.
    expect(listDocumentsMock).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(750)
    // The whole burst collapses to one authoritative document refetch.
    expect(listDocumentsMock).toHaveBeenCalledTimes(1)
  })

  it('a live ingestion.progress frame never overwrites the document-level counts (F-28 unit bug)', async () => {
    vi.useFakeTimers()
    listDocumentsMock.mockResolvedValue([doc('d1', 'ingesting'), doc('d2', 'ingesting')])
    const { api } = mountSocket()
    await vi.advanceTimersByTimeAsync(0)
    expect(api.progress.value.documentsTotal).toBe(2)

    // A chunk-granular frame for one document reports total = 47 chunks.
    emit({ type: 'ingestion.progress', processed: 10, total: 47 })
    // The frame must NOT set the corpus document total to 47.
    expect(api.progress.value.documentsTotal).toBe(2)

    // The debounced resync re-derives from the authoritative document list.
    await vi.advanceTimersByTimeAsync(750)
    expect(api.progress.value.documentsTotal).toBe(2)
    expect(api.progress.value.state).toBe('ingesting')
  })
})
