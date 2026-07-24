import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { effectScope } from 'vue'

import type { ExportStatus } from '../types'

const mockToast = vi.hoisted(() => ({ error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() }))
const api = vi.hoisted(() => ({ createExport: vi.fn(), getExport: vi.fn() }))

vi.mock('@shared/composables', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>
  return { ...actual, useToast: () => mockToast }
})
vi.mock('vue-i18n', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>
  return { ...actual, useI18n: () => ({ t: (k: string) => k }) }
})
vi.mock('../api', () => api)

import { useChatroomExport } from '../composables/useChatroomExport'

type Deferred<T> = { promise: Promise<T>; resolve: (v: T) => void }
function deferred<T>(): Deferred<T> {
  let resolve!: (v: T) => void
  const promise = new Promise<T>((r) => {
    resolve = r
  })
  return { promise, resolve }
}

function status(job_id: string, s: ExportStatus['status'], url: string | null = null): ExportStatus {
  return { job_id, chatroom_id: 'cr_1', status: s, url, error: null }
}

describe('useChatroomExport', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    api.createExport.mockReset()
    api.getExport.mockReset()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('does not write exportJob for a superseded job', async () => {
    const scope = effectScope()
    const exp = scope.run(() => useChatroomExport('cr_1'))!

    // Job A polls a pending, still-running tick; job B supersedes it.
    const aTick = deferred<ExportStatus>()
    api.getExport.mockImplementation((id: string) =>
      id === 'A' ? aTick.promise : Promise.resolve(status('B', 'running')),
    )

    api.createExport.mockResolvedValueOnce({ job_id: 'A', status: 'queued' })
    await exp.runExport()
    api.createExport.mockResolvedValueOnce({ job_id: 'B', status: 'queued' })
    await exp.runExport()

    // A's in-flight tick resolves late, for the old job.
    aTick.resolve(status('A', 'ready', 'https://minio/A'))
    await vi.advanceTimersByTimeAsync(0)

    // A's completion (ready + its download URL) must not reach the slot B owns.
    expect(exp.exportJob.value?.url).not.toBe('https://minio/A')
    expect(exp.exportJob.value?.status).not.toBe('ready')

    scope.stop()
  })

  it('cancels the previous poller when a new export starts', async () => {
    const scope = effectScope()
    const exp = scope.run(() => useChatroomExport('cr_1'))!
    api.getExport.mockResolvedValue(status('x', 'running'))

    api.createExport.mockResolvedValueOnce({ job_id: 'A', status: 'queued' })
    await exp.runExport()
    await vi.advanceTimersByTimeAsync(0)
    api.getExport.mockClear()

    api.createExport.mockResolvedValueOnce({ job_id: 'B', status: 'queued' })
    await exp.runExport()

    // Past several intervals, only B is polled; A's poller was cancelled.
    await vi.advanceTimersByTimeAsync(10_000)
    const polled = new Set(api.getExport.mock.calls.map(([id]) => id))
    expect(polled.has('A')).toBe(false)
    expect(polled.has('B')).toBe(true)

    scope.stop()
  })

  it('reset() cancels the active key and clears the slot', async () => {
    const scope = effectScope()
    const exp = scope.run(() => useChatroomExport('cr_1'))!
    api.getExport.mockResolvedValue(status('A', 'running'))

    api.createExport.mockResolvedValueOnce({ job_id: 'A', status: 'queued' })
    await exp.runExport()
    await vi.advanceTimersByTimeAsync(0)

    exp.reset()
    expect(exp.exportJob.value).toBeNull()

    api.getExport.mockClear()
    await vi.advanceTimersByTimeAsync(10_000)
    expect(api.getExport).not.toHaveBeenCalled()

    scope.stop()
  })

  it('a stale tick after reset never repopulates the slot', async () => {
    const scope = effectScope()
    const exp = scope.run(() => useChatroomExport('cr_1'))!

    const aTick = deferred<ExportStatus>()
    api.getExport.mockReturnValue(aTick.promise)
    api.createExport.mockResolvedValueOnce({ job_id: 'A', status: 'queued' })
    await exp.runExport()

    exp.reset()
    aTick.resolve(status('A', 'ready', 'https://minio/A'))
    await vi.advanceTimersByTimeAsync(0)

    expect(exp.exportJob.value).toBeNull()

    scope.stop()
  })
})
