<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { SFormField, SInput, SButton, SAlert } from '@shared/ui'
import { isProblemWithType } from '@shared/transport'
import { RateLimitError } from '@shared/errors'
import { useSessionStore } from '../stores/session'

const { t } = useI18n()
const router = useRouter()
const route = useRoute()
const session = useSessionStore()

const email = ref('')
const password = ref('')
const serverError = ref<string | null>(null)
const submitting = ref(false)
const lockoutSeconds = ref(0)
const emailRef = ref<InstanceType<typeof SInput> | null>(null)
let lockoutTimer: ReturnType<typeof setInterval> | undefined

const fieldErrors = ref<{ email?: string; password?: string }>({})

const flashVariant = ref<'info' | 'success' | null>(null)
const flashMessage = ref<string | null>(null)

function initFlash(): void {
  if (route.query.pendingVerify === '1') {
    flashVariant.value = 'info'
    flashMessage.value = t('identity.login.pendingVerify')
  } else if (route.query.passwordReset === '1') {
    flashVariant.value = 'success'
    flashMessage.value = t('identity.login.passwordResetSuccess')
  } else if (route.query.passwordChanged === '1') {
    flashVariant.value = 'success'
    flashMessage.value = t('identity.login.passwordChangedSuccess')
  }
  if (flashMessage.value) {
    router.replace({ ...route, query: {} })
  }
}

function safeRedirect(raw: string): string {
  if (!raw) return '/orgs'
  try {
    const url = new URL(raw, window.location.origin)
    if (url.origin !== window.location.origin) return '/orgs'
    return url.pathname + url.search + url.hash
  } catch {
    return '/orgs'
  }
}

function validateEmail(): boolean {
  if (!email.value.trim()) {
    fieldErrors.value.email = t('identity.validation.emailRequired')
    return false
  }
  fieldErrors.value.email = undefined
  return true
}

function validatePassword(): boolean {
  if (!password.value) {
    fieldErrors.value.password = t('identity.validation.passwordRequired')
    return false
  }
  fieldErrors.value.password = undefined
  return true
}

function startLockout(seconds: number): void {
  lockoutSeconds.value = seconds
  clearInterval(lockoutTimer)
  lockoutTimer = setInterval(() => {
    lockoutSeconds.value--
    if (lockoutSeconds.value <= 0) {
      clearInterval(lockoutTimer)
      lockoutTimer = undefined
      serverError.value = null
    }
  }, 1000)
}

onMounted(async () => {
  initFlash()
  if (session.isAuthenticated) {
    router.push('/orgs')
    return
  }
  await nextTick()
  emailRef.value?.$el?.querySelector('input')?.focus()
})

onUnmounted(() => {
  clearInterval(lockoutTimer)
})

async function submit(): Promise<void> {
  serverError.value = null
  flashMessage.value = null
  const emailValid = validateEmail()
  const passwordValid = validatePassword()
  if (!emailValid || !passwordValid) return

  submitting.value = true
  try {
    await session.login(email.value, password.value)
    const raw = (route.query.redirect as string) || ''
    router.push(safeRedirect(raw))
  } catch (e: unknown) {
    if (e instanceof RateLimitError) {
      const seconds = Math.ceil(e.retryAfterMs / 1000)
      serverError.value = t('identity.errors.lockout', { seconds })
      startLockout(seconds)
    } else if (isProblemWithType(e, '/auth/invalid-credentials')) {
      serverError.value = t('identity.errors.invalidCredentials')
      password.value = ''
      await nextTick()
      document.querySelector<HTMLInputElement>('input[type="password"]')?.focus()
    } else if (isProblemWithType(e, '/auth/lockout')) {
      serverError.value = t('identity.errors.lockout', { seconds: 60 })
      startLockout(60)
    } else if (isProblemWithType(e, '/auth/email-unverified')) {
      serverError.value = t('identity.errors.emailUnverified')
    } else if (isProblemWithType(e, '/auth/banned')) {
      serverError.value = t('identity.errors.banned')
    } else if (isProblemWithType(e, '/auth/deleted')) {
      serverError.value = t('identity.errors.accountDeleted')
    } else {
      serverError.value = t('identity.errors.generic')
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="auth-card">
    <SAlert
      v-if="flashMessage && flashVariant"
      :variant="flashVariant"
      dismissible
      class="flash-alert"
      role="status"
      @dismiss="flashMessage = null"
    >
      {{ flashMessage }}
    </SAlert>

    <h1
      id="login-heading"
      class="auth-heading"
    >
      {{ $t('identity.login.title') }}
    </h1>

    <form
      aria-labelledby="login-heading"
      @submit.prevent="submit"
    >
      <SFormField
        :label="$t('identity.login.email')"
        name="email"
        :error="fieldErrors.email"
        required
      >
        <SInput
          ref="emailRef"
          v-model="email"
          type="email"
          autocomplete="email"
          :disabled="submitting || lockoutSeconds > 0"
          :error="!!fieldErrors.email"
          @blur="validateEmail"
        />
      </SFormField>

      <SFormField
        :label="$t('identity.login.password')"
        name="password"
        :error="fieldErrors.password"
        required
      >
        <SInput
          v-model="password"
          type="password"
          autocomplete="current-password"
          :disabled="submitting || lockoutSeconds > 0"
          :error="!!fieldErrors.password"
          @blur="validatePassword"
        />
      </SFormField>

      <div class="forgot-link">
        <RouterLink :to="{ name: 'identity.passwordResetRequest' }">
          {{ $t('identity.login.forgot') }}
        </RouterLink>
      </div>

      <SAlert
        v-if="serverError"
        variant="danger"
        class="form-alert"
      >
        <span
          v-if="lockoutSeconds > 0"
          aria-live="polite"
        >
          {{ $t('identity.errors.lockout', { seconds: lockoutSeconds }) }}
        </span>
        <span v-else>{{ serverError }}</span>
      </SAlert>

      <SButton
        type="submit"
        variant="primary"
        size="md"
        :loading="submitting"
        :disabled="submitting || lockoutSeconds > 0"
        :aria-busy="submitting"
        class="form-submit"
      >
        {{ submitting ? $t('identity.login.submitting') : $t('identity.login.submit') }}
      </SButton>
    </form>
  </div>

  <p class="auth-footer">
    {{ $t('identity.login.registerPrompt') }}
    <RouterLink :to="{ name: 'identity.register' }">
      {{ $t('identity.login.registerLink') }}
    </RouterLink>
  </p>
</template>

<style scoped>
.auth-heading {
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--color-fg);
  margin: 0 0 24px;
}

form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.flash-alert {
  margin-bottom: 16px;
}

.forgot-link {
  text-align: right;
  margin-top: -8px;
}

.forgot-link a {
  font-size: 0.875rem;
  color: var(--color-accent);
  text-decoration: none;
}

.forgot-link a:hover {
  text-decoration: underline;
}

.form-alert {
  margin: 0;
}

.form-submit {
  width: 100%;
}
</style>
