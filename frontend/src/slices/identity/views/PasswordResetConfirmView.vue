<script setup lang="ts">
import { ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { authApi } from '../api/auth'

const route = useRoute()
const router = useRouter()
const newPassword = ref('')
const error = ref<string | null>(null)
const submitting = ref(false)

async function submit(): Promise<void> {
  const token = route.query.token as string | undefined
  if (!token) {
    error.value = 'missing-token'
    return
  }
  error.value = null
  submitting.value = true
  try {
    await authApi.resetPassword({ token, new_password: newPassword.value })
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
    <h1>{{ $t('identity.passwordReset.confirmTitle') }}</h1>
    <form @submit.prevent="submit">
      <label>
        {{ $t('identity.passwordReset.newPassword') }}
        <input
          v-model="newPassword"
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
        {{ $t('identity.passwordReset.confirmSubmit') }}
      </button>
    </form>
  </main>
</template>
