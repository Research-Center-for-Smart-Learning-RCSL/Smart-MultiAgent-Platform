import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { ref } from 'vue'

const mockExportToBlob = vi.fn()
const mockExportToSvg = vi.fn()

vi.mock('@excalidraw/excalidraw', () => ({
  exportToBlob: mockExportToBlob,
  exportToSvg: mockExportToSvg,
}))

import { useCanvasExport } from '../composables/useCanvasExport'

function makeApi(elementCount = 3) {
  const elements = Array.from({ length: elementCount }, (_, i) => ({ id: `el-${i}`, type: 'rectangle' }))
  return {
    getSceneElements: vi.fn(() => elements),
    getAppState: vi.fn(() => ({ theme: 'light' })),
    getFiles: vi.fn(() => ({})),
  }
}

describe('useCanvasExport', () => {
  let createObjectURLSpy: ReturnType<typeof vi.spyOn>
  let revokeObjectURLSpy: ReturnType<typeof vi.spyOn>
  let clickSpy: ReturnType<typeof vi.fn>

  beforeEach(() => {
    mockExportToBlob.mockReset()
    mockExportToSvg.mockReset()

    createObjectURLSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:test')
    revokeObjectURLSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {})
    clickSpy = vi.fn()
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => {
      if (tag === 'a') {
        return { href: '', download: '', click: clickSpy } as unknown as HTMLAnchorElement
      }
      return document.createElement(tag)
    })
    vi.spyOn(document.body, 'appendChild').mockImplementation((node) => node)
    vi.spyOn(document.body, 'removeChild').mockImplementation((node) => node)
  })

  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('exportPng calls exportToBlob with scale 1 and triggers download', async () => {
    const api = makeApi()
    const apiRef = ref(api)
    const nameRef = ref('test-room')
    const { exportPng, isExporting } = useCanvasExport(apiRef, nameRef)

    const blob = new Blob(['png'], { type: 'image/png' })
    mockExportToBlob.mockResolvedValue(blob)

    await exportPng(1)

    expect(mockExportToBlob).toHaveBeenCalledOnce()
    const opts = mockExportToBlob.mock.calls[0][0]
    expect(opts.elements).toStrictEqual(api.getSceneElements())
    expect(opts.appState).toStrictEqual(api.getAppState())
    expect(opts.files).toStrictEqual(api.getFiles())

    const dims = opts.getDimensions(100, 200)
    expect(dims).toEqual({ width: 100, height: 200, scale: 1 })

    expect(createObjectURLSpy).toHaveBeenCalledWith(blob)
    expect(clickSpy).toHaveBeenCalledOnce()
    expect(revokeObjectURLSpy).toHaveBeenCalledWith('blob:test')
    expect(isExporting.value).toBe(false)
  })

  it('exportPng at 2x doubles dimensions', async () => {
    const api = makeApi()
    const apiRef = ref(api)
    const nameRef = ref('room')
    const { exportPng } = useCanvasExport(apiRef, nameRef)

    mockExportToBlob.mockResolvedValue(new Blob(['png']))

    await exportPng(2)

    const dims = mockExportToBlob.mock.calls[0][0].getDimensions(100, 200)
    expect(dims).toEqual({ width: 200, height: 400, scale: 2 })
  })

  it('exportSvg calls exportToSvg and triggers download', async () => {
    const api = makeApi()
    const apiRef = ref(api)
    const nameRef = ref('my-room')
    const { exportSvg } = useCanvasExport(apiRef, nameRef)

    const svgEl = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
    mockExportToSvg.mockResolvedValue(svgEl)

    await exportSvg()

    expect(mockExportToSvg).toHaveBeenCalledOnce()
    const opts = mockExportToSvg.mock.calls[0][0]
    expect(opts.elements).toBe(api.getSceneElements())

    expect(createObjectURLSpy).toHaveBeenCalledOnce()
    expect(clickSpy).toHaveBeenCalledOnce()
    expect(revokeObjectURLSpy).toHaveBeenCalledWith('blob:test')
  })

  it('does nothing when excalidrawApi is null', async () => {
    const apiRef = ref(null)
    const nameRef = ref('room')
    const { exportPng, exportSvg } = useCanvasExport(apiRef, nameRef)

    await exportPng(1)
    await exportSvg()

    expect(mockExportToBlob).not.toHaveBeenCalled()
    expect(mockExportToSvg).not.toHaveBeenCalled()
  })

  it('filename contains sanitized chatroom name', async () => {
    const api = makeApi()
    const apiRef = ref(api)
    const nameRef = ref('My Room / "Special"')
    const { exportPng } = useCanvasExport(apiRef, nameRef)

    mockExportToBlob.mockResolvedValue(new Blob(['png']))

    await exportPng(1)

    const anchor = document.createElement as unknown as ReturnType<typeof vi.fn>
    const created = anchor.mock.results.find((r: { type: string }) => r.type === 'return')
    if (created) {
      const download = (created.value as HTMLAnchorElement).download
      expect(download).toMatch(/^canvas-My Room _ _Special_-\d{8}-\d{6}\.png$/)
    }
  })

  it('isExporting is true while export is in progress', async () => {
    const api = makeApi()
    const apiRef = ref(api)
    const nameRef = ref('room')
    const { exportPng, isExporting } = useCanvasExport(apiRef, nameRef)

    let resolveBlob!: (v: Blob) => void
    mockExportToBlob.mockReturnValue(new Promise<Blob>((r) => { resolveBlob = r }))

    const p = exportPng(1)
    expect(isExporting.value).toBe(true)

    resolveBlob(new Blob(['png']))
    await p
    expect(isExporting.value).toBe(false)
  })
})
