<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { SFormField, SInput, SButton, SAlert } from '@shared/ui'
import { isProblemWithType } from '@shared/transport'
import { RateLimitError } from '@shared/errors'
import { useRateLimitCountdown } from '@shared/composables'
import { useSessionStore } from '../stores/session'
import { emailSchema, validateField } from '../validation'

const { t } = useI18n()
const router = useRouter()
const route = useRoute()
const session = useSessionStore()
const rateLimit = useRateLimitCountdown()

const email = ref('')
const password = ref('')
const serverError = ref<string | null>(null)
const submitting = ref(false)
const emailRef = ref<InstanceType<typeof SInput> | null>(null)
const passwordRef = ref<InstanceType<typeof SInput> | null>(null)

const fieldErrors = ref<Record<string, string | undefined>>({})

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
    const { pendingVerify: _pv, passwordReset: _pr, passwordChanged: _pc, ...rest } = route.query
    router.replace({ ...route, query: rest })
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
  return validateField(emailSchema, email.value, fieldErrors, 'email')
}

function validatePassword(): boolean {
  if (!password.value) {
    fieldErrors.value.password = t('identity.validation.passwordRequired')
    return false
  }
  fieldErrors.value.password = undefined
  return true
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
      rateLimit.start(seconds)
    } else if (isProblemWithType(e, '/auth/invalid-credentials')) {
      serverError.value = t('identity.errors.invalidCredentials')
      password.value = ''
      await nextTick()
      passwordRef.value?.$el?.querySelector('input')?.focus()
    } else if (isProblemWithType(e, '/auth/lockout')) {
      let seconds = 60
      if (e instanceof RateLimitError) {
        seconds = Math.ceil(e.retryAfterMs / 1000)
      }
      serverError.value = t('identity.errors.lockout', { seconds })
      rateLimit.start(seconds)
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
      class="auth-form"
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
          :disabled="submitting || rateLimit.active.value"
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
          ref="passwordRef"
          v-model="password"
          type="password"
          autocomplete="current-password"
          :disabled="submitting || rateLimit.active.value"
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
      >
        <span
          v-if="rateLimit.active.value"
          aria-live="polite"
        >
          {{ $t('identity.errors.lockout', { seconds: rateLimit.seconds.value }) }}
        </span>
        <span v-else>{{ serverError }}</span>
      </SAlert>

      <SButton
        type="submit"
        variant="primary"
        size="md"
        :loading="submitting"
        :disabled="submitting || rateLimit.active.value"
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
</style>
