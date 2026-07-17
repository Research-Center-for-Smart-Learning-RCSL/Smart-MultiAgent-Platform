// Public surface of the skills slice (§31). Cross-slice imports must go through this
// barrel.
import { registerLocaleLoaders } from '@shared/i18n'

export { skillsRoutes } from './routes'
export { skillsApi } from './api'
export { skillsKeys } from './queries'
export { default as SkillBindingsPanel } from './components/SkillBindingsPanel.vue'
export { default as AdminSkillsView } from './views/AdminSkillsView.vue'
export type { SkillFileOut, SkillOut, SkillScopeRef, SkillSummaryOut } from './types'

export function installSkillsSlice(): void {
  registerLocaleLoaders({
    en: () => import('./locales/en.json'),
    'zh-TW': () => import('./locales/zh-TW.json'),
  })
}
