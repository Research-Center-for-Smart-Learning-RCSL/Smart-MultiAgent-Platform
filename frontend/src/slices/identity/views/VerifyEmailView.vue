<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { CheckCircleIcon, XCircleIcon } from '@heroicons/vue/24/outline'
import { SButton, SLoadingSpinner } from '@shared/ui'
import { isProblemWithType } from '@shared/transport'
import { authApi } from '../api/auth'
import { useSessionStore } from '../stores/session'

const { t } = useI18n()
const session = useSessionStore()

const state = ref<'verifying' | 'success' | 'failure'>('verifying')
const errorMessage = ref('')

function tokenFromHash(): string | null {
  return new URLSearchParams(window.location.hash.slice(1)).get('token')
}

onMounted(async () => {
  const token = tokenFromHash()
  if (!token) {
    state.value = 'failure'
    errorMessage.value = t('identity.verifyEmail.invalidToken')
    return
  }
  try {
    await authApi.verifyEmail(token)
    state.value = 'success'
    if (session.isAuthenticated) await session.refreshMe()
  } catch (e: unknown) {
    state.value = 'failure'
    if (isProblemWithType(e, '/auth/token-expired')) {
      errorMessage.value = t('identity.verifyEmail.expiredToken')
    } else if (isProblemWithType(e, '/auth/token-invalid')) {
      errorMessage.value = t('identity.verifyEmail.invalidToken')
    } else {
      errorMessage.value = t('identity.errors.generic')
    }
  }
})
</script>

<template>
  <div class="auth-card">
    <h1
      id="verify-heading"
      class="auth-heading"
    >
      {{ $t('identity.verifyEmail.title') }}
    </h1>

    <div
      class="verify-content"
      aria-live="polite"
    >
      <template v-if="state === 'verifying'">
        <SLoadingSpinner
          size="md"
          :text="$t('identity.verifyEmail.verifying')"
        />
      </template>

      <template v-else-if="state === 'success'">
        <CheckCircleIcon
          class="state-icon state-icon--success"
          aria-hidden="true"
        />
        <p class="state-text">
          {{ $t('identity.verifyEmail.success') }}
        </p>
        <SButton
          v-if="session.isAuthenticated"
          variant="primary"
          :to="{ path: '/orgs' }"
          as="router-link"
          class="state-action"
        >
          {{ $t('identity.verifyEmail.continue') }}
        </SButton>
        <SButton
          v-else
          variant="primary"
          :to="{ name: 'identity.login' }"
          as="router-link"
          class="state-action"
        >
          {{ $t('identity.login.title') }}
        </SButton>
      </template>

      <template v-else>
        <XCircleIcon
          class="state-icon state-icon--failure"
          aria-hidden="true"
        />
        <p
          class="state-text"
          role="alert"
        >
          {{ errorMessage }}
        </p>
        <SButton
          variant="primary"
          :to="{ name: 'identity.login' }"
          as="router-link"
          class="state-action"
        >
          {{ $t('identity.login.title') }}
        </SButton>
      </template>
    </div>
  </div>
</template>

<style scoped>
.auth-heading {
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--color-fg);
  margin: 0 0 24px;
}

.verify-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 16px;
  padding: 16px 0;
}

.state-icon {
  width: 48px;
  height: 48px;
}

.state-icon--success {
  color: var(--color-success);
}

.state-icon--failure {
  color: var(--color-danger);
}

.state-text {
  font-size: 0.875rem;
  color: var(--color-fg);
  margin: 0;
}

.state-action {
  margin-top: 8px;
}
</style>
