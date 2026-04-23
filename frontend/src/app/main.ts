import { createApp } from 'vue'
import { createPinia } from 'pinia'
import { VueQueryPlugin } from '@tanstack/vue-query'

import App from '@app/App.vue'
import { router } from '@app/router'
import { i18n } from '@shared/i18n'
import { installAdminSlice } from '@slices/admin'
import { installConversationSlice } from '@slices/conversation'
import { installIdentitySlice, useSessionStore } from '@slices/identity'
import { installKeysSlice } from '@slices/keys'
import { installTenancySlice } from '@slices/tenancy'
import { installWorkflowSlice } from '@slices/workflow'

installIdentitySlice()
installTenancySlice()
installKeysSlice()
installConversationSlice()
installWorkflowSlice()
installAdminSlice()

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.use(i18n)
app.use(VueQueryPlugin)

// Restore session from the persisted refresh token before mounting so the
// first route decision sees the right auth state (R24.12 #4).
const session = useSessionStore()
session.hydrate().finally(() => app.mount('#app'))
