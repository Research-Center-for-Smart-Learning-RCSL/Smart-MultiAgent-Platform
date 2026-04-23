<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { authApi } from '../api/auth'
import { useSessionStore } from '../stores/session'

const route = useRoute()
const state = ref<'verifying' | 'success' | 'failure'>('verifying')
const session = useSessionStore()

onMounted(async () => {
  const token = route.query.token as string | undefined
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
    <p v-if="state === 'verifying'">{{ $t('identity.verifyEmail.verifying') }}</p>
    <p v-else-if="state === 'success'">{{ $t('identity.verifyEmail.success') }}</p>
    <p v-else class="error">{{ $t('identity.verifyEmail.failure') }}</p>
  </main>
</template>
