<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { authApi } from '../api/auth'
import { useSessionStore } from '../stores/session'

const current = ref('')
const next = ref('')
const error = ref<string | null>(null)
const submitting = ref(false)
const session = useSessionStore()
const router = useRouter()

async function submit(): Promise<void> {
  error.value = null
  submitting.value = true
  try {
    await authApi.changePassword({ current: current.value, new: next.value })
    // R6.06 — server invalidated all sessions; force re-auth.
    session.clear()
    router.push({ name: 'identity.login' })
  } catch {
    error.value = 'generic'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="form-page">
    <h1>{{ $t('identity.changePassword.title') }}</h1>
    <form @submit.prevent="submit">
      <label>
        {{ $t('identity.changePassword.current') }}
        <input
          v-model="current"
          type="password"
          required
        >
      </label>
      <label>
        {{ $t('identity.changePassword.new') }}
        <input
          v-model="next"
          type="password"
          required
          minlength="10"
        >
      </label>
      <p
        v-if="error"
        class="error"
      >
        {{ error }}
      </p>
      <button
        type="submit"
        :disabled="submitting"
      >
        {{ $t('identity.changePassword.submit') }}
      </button>
    </form>
  </main>
</template>
