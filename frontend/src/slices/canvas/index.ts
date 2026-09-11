import { registerLocaleLoaders } from '@shared/i18n'

export { default as CanvasPanel } from './components/CanvasPanel.vue'
export { useCanvasSplitPane } from './composables/useCanvasSplitPane'
export { canvasKeys } from './queries'
export { canvasRoutes } from './routes'
export type { Canvas, CanvasComment, CanvasObject, CanvasSnapshot } from './types'

export function installCanvasSlice(): void {
  registerLocaleLoaders({
    en: () => import('./locales/en.json'),
    'zh-TW': () => import('./locales/zh-TW.json'),
  })
}
