import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { effectScope } from 'vue'

import { ApiError, RateLimitError, ValidationError } from '@shared/errors'
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
    mockToast.error.mockReset()
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('does not write the phase for a superseded job', async () => {
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
    expect(exp.phase.value.kind).not.toBe('ready')

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
    expect(exp.phase.value).toEqual({ kind: 'idle' })

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

    expect(exp.phase.value).toEqual({ kind: 'idle' })

    scope.stop()
  })

  describe('terminal states (F-3, F-8)', () => {
    it('a job still running after the polling window is stalled, and checkAgain() resumes it', async () => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      api.getExport.mockResolvedValue(status('A', 'running'))
      api.createExport.mockResolvedValueOnce({ job_id: 'A', status: 'queued' })
      await exp.runExport({ format: 'json' })

      // About 12 minutes of polling (Q-1); still running at the end.
      await vi.advanceTimersByTimeAsync(11 * 60_000)
      expect(exp.phase.value.kind).toBe('running')
      await vi.advanceTimersByTimeAsync(2 * 60_000)
      expect(exp.phase.value).toEqual({ kind: 'stalled', jobId: 'A', reason: 'slow' })

      // The worker finishes; "Check again" picks the same job back up.
      api.getExport.mockResolvedValue(status('A', 'ready', '/api/files/a.t'))
      exp.checkAgain()
      await vi.advanceTimersByTimeAsync(0)
      expect(exp.phase.value).toEqual({ kind: 'ready', jobId: 'A' })
      scope.stop()
    })

    it('polls fast at first and slower later', async () => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      api.getExport.mockResolvedValue(status('A', 'running'))
      api.createExport.mockResolvedValueOnce({ job_id: 'A', status: 'queued' })
      await exp.runExport()

      await vi.advanceTimersByTimeAsync(60_000)
      const firstMinute = api.getExport.mock.calls.length
      api.getExport.mockClear()
      await vi.advanceTimersByTimeAsync(10 * 60_000)
      // 3 s for the first minute; never faster than 6 s after it.
      expect(firstMinute).toBe(21)
      expect(api.getExport.mock.calls.length).toBeLessThanOrEqual(100)
      expect(api.getExport.mock.calls.length).toBeLessThan(600 / 6 + 1)
      scope.stop()
    })

    it.each([403, 404, 410])('a %i from the status endpoint fails the job after one attempt', async (code) => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      api.getExport.mockRejectedValue(
        new ApiError({ type: 'https://smap.local/problems/x', title: 'x', status: code }),
      )
      api.createExport.mockResolvedValueOnce({ job_id: 'A', status: 'queued' })
      await exp.runExport({ format: 'pdf' })
      await vi.advanceTimersByTimeAsync(30_000)

      expect(api.getExport).toHaveBeenCalledOnce()
      expect(exp.phase.value).toEqual({ kind: 'job-failed' })
      scope.stop()
    })

    it('a 500 from the status endpoint is retried, not fatal', async () => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      api.getExport.mockRejectedValueOnce(
        new ApiError({ type: 'https://smap.local/problems/internal', title: 'x', status: 500 }),
      )
      api.getExport.mockResolvedValue(status('A', 'ready', '/api/files/a.t'))
      api.createExport.mockResolvedValueOnce({ job_id: 'A', status: 'queued' })
      await exp.runExport()
      await vi.advanceTimersByTimeAsync(3000)

      expect(exp.phase.value).toEqual({ kind: 'ready', jobId: 'A' })
      scope.stop()
    })

    it('a job that fails in the worker is job-failed, and retry() returns to the form', async () => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      const opts = { format: 'json', date_range: 'last_7_days' } as const
      api.getExport.mockResolvedValue(status('A', 'failed'))
      api.createExport.mockResolvedValueOnce({ job_id: 'A', status: 'queued' })
      await exp.runExport(opts)
      await vi.advanceTimersByTimeAsync(0)

      expect(exp.phase.value).toEqual({ kind: 'job-failed' })
      // Retry goes back to the form rather than re-submitting blindly (Q-4).
      exp.retry()
      expect(exp.phase.value).toEqual({ kind: 'idle' })
      expect(api.createExport).toHaveBeenCalledOnce()
      scope.stop()
    })

    it('a create failure that is not a 422 is start-failed, and retry() re-submits', async () => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      const opts = { format: 'markdown' } as const
      api.createExport.mockRejectedValueOnce(
        new ApiError({ type: 'https://smap.local/problems/internal', title: 'x', status: 500 }),
      )
      await exp.runExport(opts)
      expect(exp.phase.value).toEqual({ kind: 'start-failed', opts })

      api.getExport.mockResolvedValue(status('B', 'running'))
      api.createExport.mockResolvedValueOnce({ job_id: 'B', status: 'queued' })
      await exp.retry()
      expect(api.createExport).toHaveBeenLastCalledWith('cr_1', opts)
      expect(exp.phase.value).toMatchObject({ kind: 'running', jobId: 'B' })
      scope.stop()
    })

    it('a 422 create is an input error, not a failure', async () => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      api.createExport.mockRejectedValueOnce(
        new ValidationError({
          type: 'https://smap.local/problems/validation',
          title: 'Unprocessable Entity',
          status: 422,
          field_errors: [{ location: 'body', path: 'body', message: 'end must not precede start' }],
        }),
      )
      await exp.runExport({ date_range: 'custom', start: '2026-10-11', end: '2026-10-01' })

      expect(exp.phase.value).toEqual({ kind: 'invalid', field: null, pdf: null })
      expect(mockToast.error).not.toHaveBeenCalled()
      scope.stop()
    })

    it('a window in which no status request succeeded is unreachable, not slow', async () => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      api.getExport.mockRejectedValue(
        new ApiError({ type: 'https://smap.local/problems/internal', title: 'x', status: 502 }),
      )
      api.createExport.mockResolvedValueOnce({ job_id: 'A', status: 'queued' })
      await exp.runExport()
      await vi.advanceTimersByTimeAsync(13 * 60_000)
      expect(exp.phase.value).toEqual({ kind: 'stalled', jobId: 'A', reason: 'unreachable' })

      // The next window answers but the job is still going: slow, not unreachable.
      api.getExport.mockResolvedValue(status('A', 'running'))
      exp.checkAgain()
      await vi.advanceTimersByTimeAsync(13 * 60_000)
      expect(exp.phase.value).toEqual({ kind: 'stalled', jobId: 'A', reason: 'slow' })
      scope.stop()
    })

    it('a 429 while polling waits for its Retry-After', async () => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      api.getExport
        .mockRejectedValueOnce(
          new RateLimitError({ type: 'https://smap.local/problems/rate-limited', title: 'x', status: 429 }, 20_000),
        )
        .mockResolvedValue(status('A', 'ready', '/api/files/a.t'))
      api.createExport.mockResolvedValueOnce({ job_id: 'A', status: 'queued' })
      await exp.runExport()
      await vi.advanceTimersByTimeAsync(19_000)
      expect(api.getExport).toHaveBeenCalledOnce()
      await vi.advanceTimersByTimeAsync(1000)
      expect(exp.phase.value).toEqual({ kind: 'ready', jobId: 'A' })
      scope.stop()
    })

    it('a 422 outside the request body names no field', async () => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      api.createExport.mockRejectedValueOnce(
        new ValidationError({
          type: 'https://smap.local/problems/validation',
          title: 'Unprocessable Entity',
          status: 422,
          field_errors: [{ location: 'query', path: 'end', message: 'x' }],
        }),
      )
      await exp.runExport()
      expect(exp.phase.value).toEqual({ kind: 'invalid', field: null, pdf: null })
      scope.stop()
    })

    it('a 422 naming a date field points at that field', async () => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      api.createExport.mockRejectedValueOnce(
        new ValidationError({
          type: 'https://smap.local/problems/validation',
          title: 'Unprocessable Entity',
          status: 422,
          field_errors: [{ location: 'body', path: 'end', message: 'invalid date' }],
        }),
      )
      await exp.runExport({ date_range: 'custom', start: '2026-10-01', end: 'x' })
      expect(exp.phase.value).toEqual({ kind: 'invalid', field: 'end', pdf: null })
      scope.stop()
    })
  })

  describe('archive-content create errors (Q-4, Q-6)', () => {
    it('a 409 for an export already in progress follows that job instead of failing', async () => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      api.createExport.mockRejectedValueOnce(
        new ApiError({
          type: 'https://smap.local/problems/conversation/export-in-progress',
          title: 'x',
          status: 409,
          job_id: 'RUNNING',
        }),
      )
      api.getExport.mockResolvedValue(status('RUNNING', 'ready', '/api/files/r.t'))
      await exp.runExport({ format: 'json' })
      await vi.advanceTimersByTimeAsync(0)

      expect(api.getExport).toHaveBeenCalledWith('RUNNING')
      expect(exp.phase.value).toEqual({ kind: 'ready', jobId: 'RUNNING' })
      scope.stop()
    })

    it('a PDF too large for the cap is an input error carrying the count', async () => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      api.createExport.mockRejectedValueOnce(
        new ValidationError({
          type: 'https://smap.local/problems/conversation/export-too-large-for-pdf',
          title: 'x',
          status: 422,
          message_count: 7200,
          limit: 5000,
        }),
      )
      await exp.runExport({ format: 'pdf' })
      expect(exp.phase.value).toEqual({
        kind: 'invalid',
        field: null,
        pdf: { count: 7200, limit: 5000 },
      })
      scope.stop()
    })
  })

  describe('superseded requests (F-9)', () => {
    it('is starting while the create is pending', async () => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      const created = deferred<{ job_id: string; status: string }>()
      api.createExport.mockReturnValueOnce(created.promise)
      const pending = exp.runExport()
      expect(exp.phase.value).toEqual({ kind: 'starting' })
      created.resolve({ job_id: 'A', status: 'queued' })
      api.getExport.mockResolvedValue(status('A', 'running'))
      await pending
      expect(exp.phase.value).toMatchObject({ kind: 'running', jobId: 'A' })
      scope.stop()
    })

    it('a create that resolves after reset() leaves the form alone', async () => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      const created = deferred<{ job_id: string; status: string }>()
      api.createExport.mockReturnValueOnce(created.promise)
      const pending = exp.runExport()

      exp.reset() // the user closed and reopened the modal
      created.resolve({ job_id: 'A', status: 'queued' })
      await pending
      await vi.advanceTimersByTimeAsync(10_000)

      expect(exp.phase.value).toEqual({ kind: 'idle' })
      expect(api.getExport).not.toHaveBeenCalled()
      scope.stop()
    })

    it('of two creates in flight, only the second job is polled', async () => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      const first = deferred<{ job_id: string; status: string }>()
      const second = deferred<{ job_id: string; status: string }>()
      api.createExport.mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise)
      api.getExport.mockResolvedValue(status('x', 'running'))

      const a = exp.runExport()
      const b = exp.runExport()
      second.resolve({ job_id: 'B', status: 'queued' })
      first.resolve({ job_id: 'A', status: 'queued' })
      await Promise.all([a, b])
      await vi.advanceTimersByTimeAsync(10_000)

      const polled = new Set(api.getExport.mock.calls.map(([id]) => id))
      expect(polled).toEqual(new Set(['B']))
      expect(exp.phase.value).toMatchObject({ kind: 'running', jobId: 'B' })
      scope.stop()
    })

    it('a create failure that resolves after reset() is ignored', async () => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      const created = deferred<{ job_id: string; status: string }>()
      api.createExport.mockReturnValueOnce(created.promise.then(() => Promise.reject(new Error('x'))))
      const pending = exp.runExport()
      exp.reset()
      created.resolve({ job_id: 'A', status: 'queued' })
      await pending
      expect(exp.phase.value).toEqual({ kind: 'idle' })
      scope.stop()
    })
  })

  describe('download()', () => {
    let downloadSpy: ReturnType<typeof vi.fn>
    beforeEach(() => {
      downloadSpy = vi.fn()
      vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
        downloadSpy(this.getAttribute('href'))
      })
    })
    afterEach(() => vi.restoreAllMocks())

    it('re-fetches the job at click time and downloads that fresh link', async () => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      api.createExport.mockResolvedValueOnce({ job_id: 'A', status: 'queued' })
      api.getExport.mockResolvedValueOnce(status('A', 'ready', '/api/files/old.t'))
      await exp.runExport()
      await vi.advanceTimersByTimeAsync(0)

      api.getExport.mockResolvedValueOnce(status('A', 'ready', '/api/files/fresh.t'))
      await exp.download()

      expect(api.getExport).toHaveBeenLastCalledWith('A')
      expect(downloadSpy).toHaveBeenCalledWith('/api/files/fresh.t')
      scope.stop()
    })

    it('reports an expired archive instead of failing silently', async () => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      api.createExport.mockResolvedValueOnce({ job_id: 'A', status: 'queued' })
      api.getExport.mockResolvedValueOnce(status('A', 'ready', '/api/files/old.t'))
      await exp.runExport()
      await vi.advanceTimersByTimeAsync(0)

      api.getExport.mockRejectedValueOnce(
        new ApiError({ type: 'https://smap.local/problems/not-found', title: 'Not Found', status: 404 }),
      )
      await exp.download()

      expect(downloadSpy).not.toHaveBeenCalled()
      expect(mockToast.error).toHaveBeenCalledWith('conversation.chatroom.exportExpired')
      scope.stop()
    })

    it('reports a failed job as a failure, not as expired', async () => {
      const scope = effectScope()
      const exp = scope.run(() => useChatroomExport('cr_1'))!
      api.createExport.mockResolvedValueOnce({ job_id: 'A', status: 'queued' })
      api.getExport.mockResolvedValueOnce(status('A', 'ready', '/api/files/old.t'))
      await exp.runExport()
      await vi.advanceTimersByTimeAsync(0)

      api.getExport.mockResolvedValueOnce(status('A', 'failed'))
      await exp.download()

      expect(downloadSpy).not.toHaveBeenCalled()
      expect(mockToast.error).toHaveBeenCalledWith('conversation.chatroom.exportDownloadFailed')
      scope.stop()
    })
  })
})
