<script setup lang="ts">
import { ref } from 'vue'
import { authApi } from '../api/auth'

const newEmail = ref('')
const password = ref('')
const done = ref(false)
const error = ref<string | null>(null)
const submitting = ref(false)

async function submit(): Promise<void> {
  error.value = null
  submitting.value = true
  try {
    await authApi.changeEmail({ new_email: newEmail.value, password: password.value })
    done.value = true
  } catch {
    error.value = 'generic'
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="form-page">
    <h1>{{ $t('identity.changeEmail.title') }}</h1>
    <form
      v-if="!done"
      @submit.prevent="submit"
    >
      <label>
        {{ $t('identity.changeEmail.newEmail') }}
        <input
          v-model="newEmail"
          type="email"
          required
        >
      </label>
      <label>
        {{ $t('identity.changeEmail.password') }}
        <input
          v-model="password"
          type="password"
          required
        >
      </label>
      <p
        v-if="error"
        class="error"
        role="alert"
      >
        {{ error }}
      </p>
      <button
        type="submit"
        class="btn btn-primary"
        :disabled="submitting"
      >
        {{ $t('identity.changeEmail.submit') }}
      </button>
    </form>
    <p v-else>
      {{ $t('identity.verifyEmail.verifying') }}
    </p>
  </main>
</template>
