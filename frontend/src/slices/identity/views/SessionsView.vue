<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useToast } from '@shared/composables'
import { authApi, type Session } from '../api/auth'

const { t } = useI18n()
const sessions = ref<Session[]>([])
const loading = ref(true)
const loadError = ref(false)
const toast = useToast()

async function load(): Promise<void> {
  loading.value = true
  loadError.value = false
  try {
    const { data } = await authApi.listSessions()
    sessions.value = data
  } catch {
    // Without this, a failed fetch rejects unhandled (global toast only) and
    // the page renders an empty list as if the user had no sessions (FE-14).
    loadError.value = true
  } finally {
    loading.value = false
  }
}

async function revoke(id: string): Promise<void> {
  try {
    await authApi.revokeSession(id)
    await load()
  } catch {
    toast.error(t('identity.sessions.revokeError'))
  }
}

onMounted(load)
</script>

<template>
  <main>
    <h1>{{ $t('identity.sessions.title') }}</h1>
    <p v-if="loading">
      …
    </p>
    <p
      v-else-if="loadError"
      role="alert"
      class="error"
    >
      {{ $t('identity.sessions.loadError') }}
      <button
        type="button"
        @click="load"
      >
        {{ $t('identity.sessions.retry') }}
      </button>
    </p>
    <ul v-else>
      <li
        v-for="s in sessions"
        :key="s.id"
      >
        <span>{{ s.user_agent || 'Unknown device' }} — {{ s.ip_inet }} — {{ s.last_used_at }}</span>
        <span v-if="s.is_current"> {{ $t('identity.sessions.currentBadge') }}</span>
        <button
          v-else
          @click="revoke(s.id)"
        >
          {{ $t('identity.sessions.revoke') }}
        </button>
      </li>
    </ul>
  </main>
</template>

<style scoped>
.error {
  color: var(--color-danger);
}
</style>
