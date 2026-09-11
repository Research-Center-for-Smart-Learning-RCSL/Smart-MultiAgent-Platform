import { ref, type Ref } from 'vue'

// Excalidraw API shape (React-in-Vue bridge has inherently weak typing)
/* eslint-disable @typescript-eslint/no-explicit-any */
type ExcalidrawAPI = {
  getSceneElements: () => readonly any[]
  getAppState: () => any
  getFiles: () => any
}
/* eslint-enable @typescript-eslint/no-explicit-any */

function sanitizeFilename(name: string): string {
  return name.replace(/[<>:"/\\|?*\x00-\x1f]/g, '_').trim() || 'canvas'
}

function buildFilename(chatroomName: string, ext: string): string {
  const safe = sanitizeFilename(chatroomName)
  const now = new Date()
  const pad = (n: number) => String(n).padStart(2, '0')
  const ts = `${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}-${pad(now.getHours())}${pad(now.getMinutes())}${pad(now.getSeconds())}`
  return `canvas-${safe}-${ts}.${ext}`
}

function triggerDownload(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

export function useCanvasExport(
  excalidrawApi: Ref<ExcalidrawAPI | null>,
  chatroomName: Ref<string>,
) {
  const isExporting = ref(false)

  async function exportPng(scale: 1 | 2) {
    const api = excalidrawApi.value
    if (!api) return

    isExporting.value = true
    try {
      const { exportToBlob } = await import('@excalidraw/excalidraw')
      const elements = api.getSceneElements()
      const appState = api.getAppState()
      const files = api.getFiles()

      const blob = await exportToBlob({
        elements,
        appState,
        files,
        getDimensions: (w: number, h: number) => ({
          width: w * scale,
          height: h * scale,
          scale,
        }),
      })

      triggerDownload(blob, buildFilename(chatroomName.value, 'png'))
    } finally {
      isExporting.value = false
    }
  }

  async function exportSvg() {
    const api = excalidrawApi.value
    if (!api) return

    isExporting.value = true
    try {
      const { exportToSvg } = await import('@excalidraw/excalidraw')
      const elements = api.getSceneElements()
      const appState = api.getAppState()
      const files = api.getFiles()

      const svg = await exportToSvg({ elements, appState, files })
      const serializer = new XMLSerializer()
      const svgString = serializer.serializeToString(svg)
      const blob = new Blob([svgString], { type: 'image/svg+xml;charset=utf-8' })

      triggerDownload(blob, buildFilename(chatroomName.value, 'svg'))
    } finally {
      isExporting.value = false
    }
  }

  return { exportPng, exportSvg, isExporting }
}
