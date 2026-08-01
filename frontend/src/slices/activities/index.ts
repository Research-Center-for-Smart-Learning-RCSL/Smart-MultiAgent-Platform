// Public surface of the activities slice (R30.17-R30.19). Only exports listed
// here are importable from other slices / the app shell (enforced via
// eslint-plugin-boundaries). The chatroom WS switch in slices/conversation
// consumes `useActivitiesStore` (Q-3: conversation depends on activities).

import { registerLocaleLoaders } from '@shared/i18n'

// Host + presentational components (consumed by the in-chatroom activation UI /
// first-party plugin surface, which is out of scope for this SDK task).
export { default as ActivityHost } from './components/ActivityHost.vue'
export { default as ActivityPanel } from './components/ActivityPanel.vue'
export { default as ActivityOutcomeBadge } from './components/ActivityOutcomeBadge.vue'

// Realtime state — driven by the conversation WS switch.
export { useActivitiesStore } from './stores/activities'

// Plugin SDK for first-party plugin authors.
export { defineActivityPlugin } from './sdk/defineActivityPlugin'
export { getActivityPlugin, registerActivityPlugin } from './plugins'
export { InProcessBridge, IframeBridge } from './sdk/bridge'
export type { HostBridge, BridgeMountOptions } from './sdk/bridge'
export type {
  ActivityPlugin,
  ActivityPluginManifest,
  ActivityRenderCtx,
  ActivitySessionRef,
  ActivitySubmissionResult,
  ActivityTranslate,
  HostToPluginMessage,
  JSONSchema,
  PluginToHostMessage,
} from './sdk/types'

export { activitiesRoutes } from './routes'
export { activityKeys } from './queries'
export { getActiveActivation, getRoomActivityType } from './api'
export type {
  ActivityActivation,
  ActivityOutcome,
  ActivityType,
  ActivityTypePublic,
  ActivityValidationStatus,
  ActivationView,
} from './types'

export function installActivitiesSlice(): void {
  registerLocaleLoaders({
    en: () => import('./locales/en.json'),
    'zh-TW': () => import('./locales/zh-TW.json'),
  })
}
