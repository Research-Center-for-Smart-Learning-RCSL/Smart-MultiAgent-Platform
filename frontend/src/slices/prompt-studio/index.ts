// Public surface of the prompt-studio slice (§29). Cross-slice imports must go
// through this barrel.
import { registerLocaleLoaders } from '@shared/i18n'

export { promptStudioRoutes } from './routes'
export { promptStudioApi } from './api'
export { promptStudioKeys } from './queries'
export { default as PromptAssistantPanel } from './components/PromptAssistantPanel.vue'
export { default as PromptTemplatePicker } from './components/PromptTemplatePicker.vue'
export { default as AdminPromptStudioView } from './views/AdminPromptStudioView.vue'
export type { AssistantConfig, PromptTemplate, ResolvedAssistant } from './types'

export function installPromptStudioSlice(): void {
  registerLocaleLoaders({
    en: () => import('./locales/en.json'),
    'zh-TW': () => import('./locales/zh-TW.json'),
  })
}
