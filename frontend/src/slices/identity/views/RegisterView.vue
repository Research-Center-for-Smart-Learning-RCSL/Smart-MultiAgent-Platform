<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { SFormField, SInput, SButton, SAlert } from '@shared/ui'
import { isProblemWithType } from '@shared/transport'
import { RateLimitError } from '@shared/errors'
import { useRateLimitCountdown } from '@shared/composables'
import { authApi, type CaptchaConfig } from '../api/auth'
import { emailSchema, passwordSchema, validateField } from '../validation'
import CaptchaWidget from '../components/CaptchaWidget.vue'

const { t } = useI18n()
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

function validateEmail(): boolean {
  return validateField(emailSchema, email.value, fieldErrors, 'email', t)
}

function validatePassword(): boolean {
  return validateField(passwordSchema, password.value, fieldErrors, 'password', t)
}

onMounted(async () => {
  const focusPromise = nextTick().then(() => {
    emailRef.value?.$el?.querySelector('input')?.focus()
  })
  try {
    const { data } = await authApi.captchaConfig()
    captcha.value = data
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

  if (captcha.value.provider !== 'off' && !captchaToken.value) {
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
    router.push({ name: 'identity.login', query: { pendingVerify: '1' } })
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
  <div class="auth-card">
    <h1
      id="register-heading"
      class="auth-heading"
    >
      {{ $t('identity.register.title') }}
    </h1>

    <form
      class="auth-form"
      aria-labelledby="register-heading"
      @submit.prevent="submit"
    >
      <SFormField
        :label="$t('identity.register.email')"
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
        :label="$t('identity.register.password')"
        name="password"
        :error="fieldErrors.password"
        :help="$t('identity.register.passwordHelp')"
        required
      >
        <SInput
          v-model="password"
          type="password"
          autocomplete="new-password"
          :disabled="submitting || rateLimit.active.value"
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
      >
        {{ serverError }}
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
        {{ submitting ? $t('identity.register.submitting') : $t('identity.register.submit') }}
      </SButton>
    </form>
  </div>

  <p class="auth-footer">
    {{ $t('identity.register.loginPrompt') }}
    <RouterLink :to="{ name: 'identity.login' }">
      {{ $t('identity.register.loginLink') }}
    </RouterLink>
  </p>
</template>

<style scoped>
.field-error {
  font-size: 0.75rem;
  color: var(--color-danger);
  margin: -8px 0 0;
}
</style>
