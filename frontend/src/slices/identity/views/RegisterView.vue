<script setup lang="ts">
import { ref, computed, onMounted, nextTick } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { SAuthCard, SFormField, SInput, SButton, SAlert } from '@shared/ui'
import { INPUT_LIMITS } from '@shared/constants/inputLimits'
import { isProblemWithType } from '@shared/transport'
import { RateLimitError } from '@shared/errors'
import { useRateLimitCountdown, safeRedirect } from '@shared/composables'
import { authApi, type CaptchaConfig } from '../api/auth'
import { emailSchema, passwordSchema, validateField, errorAttrs } from '../validation'
import CaptchaWidget from '../components/CaptchaWidget.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const rateLimit = useRateLimitCountdown()

const email = ref('')
const password = ref('')
const captchaToken = ref('')
const captcha = ref<CaptchaConfig>({ mode: 'off', provider: 'off', sitekey: '' })
const serverError = ref<string | null>(null)
const submitting = ref(false)
const emailRef = ref<InstanceType<typeof SInput> | null>(null)

const fieldErrors = ref<Record<string, string | undefined>>({})

const loginLinkTo = computed(() => {
  const raw = route.query.redirect as string | undefined
  const redirect = raw ? safeRedirect(raw) : undefined
  if (redirect && redirect !== '/orgs') return { name: 'identity.login', query: { redirect } }
  return { name: 'identity.login' }
})

function validateEmail(): boolean {
  return validateField(emailSchema, email.value, fieldErrors, 'email', t)
}

function validatePassword(): boolean {
  return validateField(passwordSchema, password.value, fieldErrors, 'password', t)
}

function signInWithGoogle(): void {
  // Same backend authorize endpoint as login: Google login provisions a new
  // account on first sign-in, so one button serves both register and login.
  window.location.assign('/api/auth/google/authorize')
}

onMounted(async () => {
  const focusPromise = nextTick().then(() => {
    emailRef.value?.$el?.querySelector('input')?.focus()
  })
  try {
    captcha.value = await authApi.captchaConfig()
  } catch {
    // Config unreachable -- fail-open per backend design
  }
  await focusPromise
})

async function submit(): Promise<void> {
  serverError.value = null
  const emailValid = validateEmail()
  const passwordValid = validatePassword()
  if (!emailValid || !passwordValid) return

  // Gate on `mode`, not the renderable `provider`: when captcha is enforced
  // (mode==='on') but the provider is one the widget can't render (coerced to
  // 'off'), no widget shows -- still require a token so we surface "captcha
  // required" here instead of posting an empty token the backend will reject.
  if (captcha.value.mode === 'on' && !captchaToken.value) {
    fieldErrors.value.captcha = t('identity.errors.captchaRequired')
    return
  }
  fieldErrors.value.captcha = undefined

  submitting.value = true
  try {
    await authApi.register({
      email: email.value,
      password: password.value,
      captcha_token: captchaToken.value,
    })
    const loginQuery: Record<string, string> = { pendingVerify: '1' }
    const raw = route.query.redirect as string | undefined
    const redirect = raw ? safeRedirect(raw) : undefined
    if (redirect && redirect !== '/orgs') loginQuery.redirect = redirect
    router.push({ name: 'identity.login', query: loginQuery })
  } catch (e: unknown) {
    if (e instanceof RateLimitError) {
      const seconds = Math.ceil(e.retryAfterMs / 1000)
      serverError.value = t('identity.errors.rateLimit')
      rateLimit.start(seconds)
    } else if (isProblemWithType(e, '/auth/email-taken')) {
      serverError.value = t('identity.errors.emailTaken')
      email.value = ''
      await nextTick()
      emailRef.value?.$el?.querySelector('input')?.focus()
    } else if (isProblemWithType(e, '/auth/domain-denied')) {
      serverError.value = t('identity.errors.domainDenied')
      email.value = ''
    } else if (isProblemWithType(e, '/auth/password-weak')) {
      serverError.value = t('identity.errors.weakPassword')
      password.value = ''
    } else if (isProblemWithType(e, '/auth/captcha-required')) {
      serverError.value = t('identity.errors.captchaRequired')
    } else if (isProblemWithType(e, '/auth/email-invalid')) {
      serverError.value = t('identity.errors.emailInvalid')
      email.value = ''
    } else {
      serverError.value = t('identity.errors.generic')
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <SAuthCard
    :title="$t('identity.register.title')"
    title-id="register-heading"
  >
    <form
      class="form-stack"
      aria-labelledby="register-heading"
      @submit.prevent="submit"
    >
      <SFormField
        :label="$t('identity.register.email')"
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
        :label="$t('identity.register.password')"
        name="password"
        v-bind="errorAttrs(fieldErrors.password)"
        :help="$t('identity.register.passwordHelp')"
        required
      >
        <SInput
          v-model="password"
          type="password"
          autocomplete="new-password"
          :maxlength="INPUT_LIMITS.PASSWORD"
          :disabled="!!(submitting || rateLimit.active.value)"
          :error="!!fieldErrors.password"
          @blur="validatePassword"
        />
      </SFormField>

      <CaptchaWidget
        v-if="captcha.provider !== 'off'"
        :provider="captcha.provider"
        :sitekey="captcha.sitekey"
        @update:token="captchaToken = $event"
      />
      <p
        v-if="fieldErrors.captcha"
        class="field-error"
        role="alert"
      >
        {{ fieldErrors.captcha }}
      </p>

      <SAlert
        v-if="serverError"
        variant="danger"
        focus-on-mount
      >
        {{ serverError }}
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
        {{ submitting ? $t('identity.register.submitting') : $t('identity.register.submit') }}
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
      {{ $t('identity.register.loginPrompt') }}
      <RouterLink :to="loginLinkTo">
        {{ $t('identity.register.loginLink') }}
      </RouterLink>
    </template>
  </SAuthCard>
</template>

<style scoped>
.form-stack {
  display: flex;
  flex-direction: column;
  gap: var(--space-4);
}

.submit-btn {
  width: 100%;
}

.field-error {
  font-size: var(--font-size-xs);
  color: var(--color-danger);
  margin: -8px 0 0;
}

.oauth-divider {
  display: flex;
  align-items: center;
  text-align: center;
  color: var(--color-muted);
  font-size: var(--font-size-code);
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
