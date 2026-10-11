import { beforeEach, describe, expect, it, vi } from 'vitest'
import { effectScope, ref } from 'vue'

const handlers = new Map<string, (data: Record<string, unknown>) => void>()
const get = vi.fn()

vi.mock('@shared/transport/ws-manager', () => ({
  wsManager: {
    channel: () => ({
      subscribe: (event: string, fn: (data: Record<string, unknown>) => void) => {
        handlers.set(event, fn)
        return () => handlers.delete(event)
      },
    }),
  },
}))
vi.mock('@shared/transport', () => ({ http: { get } }))
vi.mock('@tanstack/vue-query', () => ({ useQueryClient: () => ({ invalidateQueries: vi.fn() }) }))

const { useCanvasSocket } = await import('../useCanvasSocket')

describe('useCanvasSocket canvas.image_added', () => {
  beforeEach(() => {
    handlers.clear()
    get.mockReset()
  })

  it('mints its own image link, then fetches it as an absolute same-origin path', async () => {
    get.mockImplementation(async (url: string) =>
      url.endsWith('/link')
        ? { data: { url: '/api/files/abc.def' } }
        : { data: new Blob(['x'], { type: 'image/png' }) },
    )
    const addFiles = vi.fn()
    const scope = effectScope()
    scope.run(() => useCanvasSocket(ref('room-1'), () => ({ addFiles })))

    // A room-wide broadcast: a shared link would hit its per-link stream cap.
    handlers.get('canvas.image_added')?.({ fileId: 'f1', mimeType: 'image/png' })
    await vi.waitFor(() => expect(get).toHaveBeenCalledTimes(2))

    expect(get.mock.calls[0][0]).toBe('/chatrooms/room-1/canvas/images/f1/link')
    const [url, config] = get.mock.calls[1]
    expect(url).toBe('/api/files/abc.def')
    // The shared instance's baseURL is '/api'; without this override axios
    // requests /api/api/files/... (browser-file-delivery §5).
    expect(config).toMatchObject({ baseURL: '', responseType: 'blob' })
    await vi.waitFor(() => expect(addFiles).toHaveBeenCalledOnce())
    expect(addFiles.mock.calls[0][0][0]).toMatchObject({ id: 'f1', mimeType: 'image/png' })
    scope.stop()
  })
})
