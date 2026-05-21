<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { authApi } from '../api/auth'
import { useSessionStore } from '../stores/session'

const state = ref<'verifying' | 'success' | 'failure'>('verifying')
const session = useSessionStore()

// The verification token arrives in the URL fragment (`#token=…`) so it never
// reaches the server's logs or `Referer` headers — read it from the hash and
// POST it, never from the query string (SEC-8).
function tokenFromHash(): string | null {
  return new URLSearchParams(window.location.hash.slice(1)).get('token')
}

onMounted(async () => {
  const token = tokenFromHash()
  if (!token) {
    state.value = 'failure'
    return
  }
  try {
    await authApi.verifyEmail(token)
    state.value = 'success'
    if (session.isAuthenticated) await session.refreshMe()
  } catch {
    state.value = 'failure'
  }
})
</script>

<template>
  <main class="form-page">
    <h1>{{ $t('identity.verifyEmail.title') }}</h1>
    <p v-if="state === 'verifying'">
      {{ $t('identity.verifyEmail.verifying') }}
    </p>
    <p v-else-if="state === 'success'">
      {{ $t('identity.verifyEmail.success') }}
    </p>
    <p
      v-else
      class="error"
    >
      {{ $t('identity.verifyEmail.failure') }}
    </p>
  </main>
</template>
