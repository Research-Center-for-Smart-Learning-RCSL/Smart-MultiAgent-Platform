<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { authApi } from '../api/auth'
import { isProblemWithType } from '@shared/transport'

const { t } = useI18n()
const email = ref('')
const password = ref('')
const captchaToken = ref('')
const error = ref<string | null>(null)
const submitting = ref(false)
const router = useRouter()

async function submit(): Promise<void> {
  error.value = null
  submitting.value = true
  try {
    await authApi.register({
      email: email.value,
      password: password.value,
      captcha_token: captchaToken.value,
    })
    router.push({ name: 'identity.login', query: { pendingVerify: '1' } })
  } catch (e: unknown) {
    if (isProblemWithType(e, '/auth/captcha-required')) error.value = t('identity.errors.captcha')
    else if (isProblemWithType(e, '/auth/password-weak')) error.value = t('identity.errors.weakPassword')
    else if (isProblemWithType(e, '/auth/domain-denied')) error.value = t('identity.errors.domain')
    else error.value = t('identity.errors.generic')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="form-page">
    <h1>{{ $t('identity.register.title') }}</h1>
    <form @submit.prevent="submit">
      <label>
        {{ $t('identity.register.email') }}
        <input
          v-model="email"
          type="email"
          required
          autocomplete="email"
        >
      </label>
      <label>
        {{ $t('identity.register.password') }}
        <input
          v-model="password"
          type="password"
          required
          autocomplete="new-password"
          minlength="10"
        >
      </label>
      <label>
        {{ $t('identity.register.captcha') }}
        <input
          v-model="captchaToken"
          :placeholder="$t('identity.register.captchaPlaceholder')"
          required
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
        {{ $t('identity.register.submit') }}
      </button>
    </form>
    <router-link :to="{ name: 'identity.login' }">
      {{ $t('identity.register.loginLink') }}
    </router-link>
  </main>
</template>
