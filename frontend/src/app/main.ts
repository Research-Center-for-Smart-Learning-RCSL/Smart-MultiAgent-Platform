import '@shared/styles/main.css'
import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'
import { queryClient } from '@shared/query-client'

import App from '@app/App.vue'
import { installErrorHandler } from '@app/errorHandler'
import { router } from '@app/router'
import { i18n, registerLocaleLoaders, ensureLocaleLoaded, syncHtmlLang, type Locale } from '@shared/i18n'
import { installAdminSlice } from '@slices/admin'
import { installAgentsSlice } from '@slices/agents'
import { installConversationSlice } from '@slices/conversation'
import { installIdentitySlice, useSessionStore } from '@slices/identity'
import { installKeysSlice } from '@slices/keys'
import { installNotificationsSlice } from '@slices/notifications'
import { installTenancySlice } from '@slices/tenancy'
import { installWorkflowSlice } from '@slices/workflow'

// App-shell strings load with the active language, same as every slice.
registerLocaleLoaders({
  en: () => import('@app/locales/en.json'),
  'zh-TW': () => import('@app/locales/zh-TW.json'),
})

installIdentitySlice()
installTenancySlice()
installKeysSlice()
installAgentsSlice()
installConversationSlice()
installWorkflowSlice()
installAdminSlice()
installNotificationsSlice()

const app = createApp(App)
installErrorHandler(app)
app.use(createPinia())
app.use(i18n)
app.use(VueQueryPlugin, { queryClient })

syncHtmlLang()

// Restore session from the persisted refresh token BEFORE installing the router
// (R24.12 #4). Vue Router fires its initial navigation — and therefore the auth
// guard — at install time, so installing it before the boot refresh resolves
// makes the guard observe an unauthenticated session and bounce a logged-in
// user (deep link / hard reload) to /login, never recovering once hydrate lands.
// Gating router install + mount on hydrate makes the first route decision see
// the real auth state.
const session = useSessionStore()
// Gate mount on BOTH session hydrate (the router guard needs real auth state)
// and the active-language message bundles (avoid a flash of untranslated keys).
Promise.allSettled([
  session.hydrate(),
  ensureLocaleLoaded(i18n.global.locale.value as Locale),
]).finally(() => {
  app.use(router)
  app.mount('#app')
})

// Re-hydrate when the user returns to the tab so auth state stays fresh.
document.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'visible') session.hydrate()
})
