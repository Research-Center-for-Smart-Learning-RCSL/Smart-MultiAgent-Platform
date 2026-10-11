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

  it('fetches the image link as an absolute same-origin path, not under the /api base', async () => {
    get.mockResolvedValue({ data: new Blob(['x'], { type: 'image/png' }) })
    const addFiles = vi.fn()
    const scope = effectScope()
    scope.run(() => useCanvasSocket(ref('room-1'), () => ({ addFiles })))

    handlers.get('canvas.image_added')?.({ fileId: 'f1', url: '/api/files/abc.def', mimeType: 'image/png' })

    expect(get).toHaveBeenCalledOnce()
    const [url, config] = get.mock.calls[0]
    expect(url).toBe('/api/files/abc.def')
    // The shared instance's baseURL is '/api'; without this override axios
    // requests /api/api/files/... (browser-file-delivery §5).
    expect(config).toMatchObject({ baseURL: '', responseType: 'blob' })
    scope.stop()
  })
})
