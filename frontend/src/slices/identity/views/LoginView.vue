<script setup lang="ts">
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useSessionStore } from '../stores/session'
import { isProblemWithType } from '@shared/transport'

const { t } = useI18n()
const email = ref('')
const password = ref('')
const error = ref<string | null>(null)
const submitting = ref(false)
const session = useSessionStore()
const router = useRouter()
const route = useRoute()

async function submit(): Promise<void> {
  error.value = null
  submitting.value = true
  try {
    await session.login(email.value, password.value)
    const raw = (route.query.redirect as string) || ''
    const redirect = raw.startsWith('/') && !raw.startsWith('//') ? raw : '/orgs'
    router.push(redirect)
  } catch (e: unknown) {
    if (isProblemWithType(e, '/auth/invalid-credentials')) error.value = t('identity.errors.invalid')
    else if (isProblemWithType(e, '/auth/lockout')) error.value = t('identity.errors.lockout')
    else if (isProblemWithType(e, '/auth/email-unverified')) error.value = t('identity.errors.unverified')
    else error.value = t('identity.errors.generic')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <main class="form-page">
    <h1>{{ $t('identity.login.title') }}</h1>
    <p v-if="route.query.pendingVerify">
      {{ $t('identity.verifyEmail.verifying') }}
    </p>
    <form @submit.prevent="submit">
      <label>
        {{ $t('identity.login.email') }}
        <input
          v-model="email"
          type="email"
          required
          autocomplete="email"
        >
      </label>
      <label>
        {{ $t('identity.login.password') }}
        <input
          v-model="password"
          type="password"
          required
          autocomplete="current-password"
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
        {{ $t('identity.login.submit') }}
      </button>
    </form>
    <div class="links">
      <router-link :to="{ name: 'identity.register' }">
        {{ $t('identity.login.registerLink') }}
      </router-link>
      <router-link :to="{ name: 'identity.passwordResetRequest' }">
        {{ $t('identity.login.forgot') }}
      </router-link>
    </div>
  </main>
</template>
