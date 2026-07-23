<script setup lang="ts">
import { ref, computed, onMounted, nextTick } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { SAuthCard, SFormField, SInput, SButton, SAlert } from '@shared/ui'
import { INPUT_LIMITS } from '@shared/constants/inputLimits'
import { isProblemWithType } from '@shared/transport'
import { RateLimitError } from '@shared/errors'
import { useRateLimitCountdown, safeRedirect } from '@shared/composables'
import { useSessionStore } from '../stores/session'
import { emailSchema, validateField, errorAttrs } from '../validation'

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

const registerLinkTo = computed(() => {
  const redirect = route.query.redirect as string | undefined
  if (redirect) return { name: 'identity.register', query: { redirect } }
  return { name: 'identity.register' }
})

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

function initOauthError(): void {
  const code = route.query.oauth_error as string | undefined
  if (!code) return
  const map: Record<string, string> = {
    'oauth-unavailable': t('identity.login.oauthError.unavailable'),
    'oauth-denied': t('identity.login.oauthError.denied'),
    'oauth-email-unverified': t('identity.login.oauthError.emailUnverified'),
    banned: t('identity.errors.banned'),
    deleted: t('identity.errors.accountDeleted'),
  }
  serverError.value = map[code] ?? t('identity.login.oauthError.generic')
  const { oauth_error: _oe, ...rest } = route.query
  router.replace({ ...route, query: rest })
}

function signInWithGoogle(): void {
  // Full-page navigation to the backend authorize endpoint (which 302s to Google).
  // A top-level navigation cannot carry the bearer header — login mode needs none.
  window.location.assign('/api/auth/google/authorize')
}


function validateEmail(): boolean {
  return validateField(emailSchema, email.value, fieldErrors, 'email', t)
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
  initOauthError()
  if (session.isAuthenticated) {
    const raw = (route.query.redirect as string) || ''
    router.push(safeRedirect(raw))
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
  <SAuthCard>
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
      class="card-heading"
    >
      {{ $t('identity.login.title') }}
    </h1>

    <form
      class="form-stack"
      aria-labelledby="login-heading"
      @submit.prevent="submit"
    >
      <SFormField
        :label="$t('identity.login.email')"
        name="email"
        v-bind="errorAttrs(fieldErrors.email)"
        required
      >
        <SInput
          ref="emailRef"
          v-model="email"
          type="email"
          autocomplete="email"
          :maxlength="INPUT_LIMITS.EMAIL"
          :disabled="!!(submitting || rateLimit.active.value)"
          :error="!!fieldErrors.email"
          @blur="validateEmail"
        />
      </SFormField>

      <SFormField
        :label="$t('identity.login.password')"
        name="password"
        v-bind="errorAttrs(fieldErrors.password)"
        required
      >
        <SInput
          ref="passwordRef"
          v-model="password"
          type="password"
          autocomplete="current-password"
          :maxlength="INPUT_LIMITS.PASSWORD"
          :disabled="!!(submitting || rateLimit.active.value)"
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
        focus-on-mount
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
        :disabled="!!(submitting || rateLimit.active.value)"
        :aria-busy="submitting"
        class="submit-btn"
      >
        {{ submitting ? $t('identity.login.submitting') : $t('identity.login.submit') }}
      </SButton>
    </form>

    <div
      class="oauth-divider"
      role="separator"
    >
      <span>{{ $t('identity.login.orDivider') }}</span>
    </div>

    <SButton
      type="button"
      variant="secondary"
      size="md"
      class="submit-btn"
      :disabled="!!(submitting || rateLimit.active.value)"
      @click="signInWithGoogle"
    >
      <template #icon-left>
        <svg
          width="18"
          height="18"
          viewBox="0 0 18 18"
          aria-hidden="true"
        >
          <path
            fill="#4285F4"
            d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92A8.78 8.78 0 0 0 17.64 9.2z"
          />
          <path
            fill="#34A853"
            d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.8.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18z"
          />
          <path
            fill="#FBBC05"
            d="M3.97 10.72a5.41 5.41 0 0 1 0-3.44V4.95H.96a9 9 0 0 0 0 8.1l3.01-2.33z"
          />
          <path
            fill="#EA4335"
            d="M9 3.58c1.32 0 2.5.45 3.44 1.35l2.58-2.58C13.47.9 11.43 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33C4.68 5.16 6.66 3.58 9 3.58z"
          />
        </svg>
      </template>
      {{ $t('identity.login.googleSignIn') }}
    </SButton>

    <template #footer>
      {{ $t('identity.login.registerPrompt') }}
      <RouterLink :to="registerLinkTo">
        {{ $t('identity.login.registerLink') }}
      </RouterLink>
    </template>
  </SAuthCard>
</template>

<style scoped>
.card-heading {
  font-size: var(--font-size-2xl);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
  margin: 0 0 var(--space-6);
}

.form-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.submit-btn {
  width: 100%;
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

.oauth-divider {
  display: flex;
  align-items: center;
  text-align: center;
  color: var(--color-muted);
  font-size: 0.8125rem;
  margin: var(--space-4) 0;
}

.oauth-divider::before,
.oauth-divider::after {
  content: '';
  flex: 1;
  border-top: 1px solid var(--color-border);
}

.oauth-divider span {
  padding: 0 var(--space-3);
}
</style>
