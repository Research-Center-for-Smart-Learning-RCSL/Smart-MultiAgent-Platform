import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'

import App from '@app/App.vue'
import { installErrorHandler } from '@app/errorHandler'
import { router } from '@app/router'
import { i18n, registerMessages, syncHtmlLang } from '@shared/i18n'
import appEn from '@app/locales/en.json'
import appZh from '@app/locales/zh-TW.json'
import { installAdminSlice } from '@slices/admin'
import { installAgentsSlice } from '@slices/agents'
import { installConversationSlice } from '@slices/conversation'
import { installIdentitySlice, useSessionStore } from '@slices/identity'
import { installKeysSlice } from '@slices/keys'
import { installTenancySlice } from '@slices/tenancy'
import { installWorkflowSlice } from '@slices/workflow'

registerMessages('en', appEn)
registerMessages('zh-TW', appZh)

installIdentitySlice()
installTenancySlice()
installKeysSlice()
installAgentsSlice()
installConversationSlice()
installWorkflowSlice()
installAdminSlice()

const app = createApp(App)
installErrorHandler(app)
app.use(createPinia())
app.use(router)
app.use(i18n)
app.use(VueQueryPlugin)

syncHtmlLang()

// Restore session from the persisted refresh token before mounting so the
// first route decision sees the right auth state (R24.12 #4).
const session = useSessionStore()
session.hydrate().finally(() => app.mount('#app'))
