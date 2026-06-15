<script setup lang="ts">
import { ref } from 'vue'
import { authApi } from '../api/auth'

const email = ref('')
const sent = ref(false)
const submitting = ref(false)

async function submit(): Promise<void> {
  submitting.value = true
  try {
    // Endpoint is intentionally 204 regardless of whether the address exists
    // (R6.05) — no enumeration.
    await authApi.requestPasswordReset(email.value)
    sent.value = true
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="form-page">
    <h1>{{ $t('identity.passwordReset.requestTitle') }}</h1>
    <form
      v-if="!sent"
      @submit.prevent="submit"
    >
      <label>
        {{ $t('identity.login.email') }}
        <input
          v-model="email"
          type="email"
          required
          autocomplete="email"
        >
      </label>
      <button
        type="submit"
        class="btn btn-primary"
        :disabled="submitting"
      >
        {{ $t('identity.passwordReset.requestSubmit') }}
      </button>
    </form>
    <p v-else>
      {{ $t('identity.verifyEmail.success') }}
    </p>
  </main>
</template>
