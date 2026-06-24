<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { EnvelopeIcon } from '@heroicons/vue/24/outline'
import { SPageHeader, SCard, SFormField, SInput, SButton, SAlert } from '@shared/ui'
import { isProblemWithType } from '@shared/transport'
import { RateLimitError } from '@shared/errors'
import { authApi } from '../api/auth'
import { useSessionStore } from '../stores/session'

const { t } = useI18n()
const session = useSessionStore()

const newEmail = ref('')
const password = ref('')
const done = ref(false)
const submittedEmail = ref('')
const serverError = ref<string | null>(null)
const submitting = ref(false)
const rateLimitSeconds = ref(0)
const emailRef = ref<InstanceType<typeof SInput> | null>(null)
let rateLimitTimer: ReturnType<typeof setInterval> | undefined

const fieldErrors = ref<{ newEmail?: string; password?: string }>({})

function validateNewEmail(): boolean {
  if (!newEmail.value.trim()) {
    fieldErrors.value.newEmail = t('identity.validation.emailRequired')
    return false
  }
  const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
  if (!emailPattern.test(newEmail.value)) {
    fieldErrors.value.newEmail = t('identity.validation.emailFormat')
    return false
  }
  if (newEmail.value === session.me?.email) {
    fieldErrors.value.newEmail = t('identity.validation.emailSame')
    return false
  }
  fieldErrors.value.newEmail = undefined
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

function startRateLimitCountdown(seconds: number): void {
  rateLimitSeconds.value = seconds
  clearInterval(rateLimitTimer)
  rateLimitTimer = setInterval(() => {
    rateLimitSeconds.value--
    if (rateLimitSeconds.value <= 0) {
      clearInterval(rateLimitTimer)
      rateLimitTimer = undefined
      serverError.value = null
    }
  }, 1000)
}

onMounted(async () => {
  await nextTick()
  emailRef.value?.$el?.querySelector('input')?.focus()
})

onUnmounted(() => {
  clearInterval(rateLimitTimer)
})

async function submit(): Promise<void> {
  serverError.value = null
  const emailValid = validateNewEmail()
  const pwValid = validatePassword()
  if (!emailValid || !pwValid) return

  submitting.value = true
  try {
    await authApi.changeEmail({ new_email: newEmail.value, password: password.value })
    submittedEmail.value = newEmail.value
    done.value = true
  } catch (e: unknown) {
    if (e instanceof RateLimitError) {
      const seconds = Math.ceil(e.retryAfterMs / 1000)
      serverError.value = t('identity.errors.rateLimit')
      startRateLimitCountdown(seconds)
    } else if (isProblemWithType(e, '/auth/invalid-credentials')) {
      fieldErrors.value.password = t('identity.errors.invalidCredentials')
      password.value = ''
      await nextTick()
      document.querySelector<HTMLInputElement>('input[type="password"]')?.focus()
    } else if (isProblemWithType(e, '/auth/email-taken')) {
      serverError.value = t('identity.errors.emailTaken')
      newEmail.value = ''
      await nextTick()
      emailRef.value?.$el?.querySelector('input')?.focus()
    } else if (isProblemWithType(e, '/auth/domain-denied')) {
      serverError.value = t('identity.errors.domainDenied')
      newEmail.value = ''
    } else if (isProblemWithType(e, '/auth/email-invalid')) {
      serverError.value = t('identity.errors.emailInvalid')
      newEmail.value = ''
    } else {
      serverError.value = t('identity.errors.generic')
    }
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div>
    <SPageHeader :title="$t('identity.changeEmail.title')" />

    <SCard class="form-card">
      <template v-if="!done">
        <dl class="current-email">
          <dt>{{ $t('identity.changeEmail.currentLabel') }}</dt>
          <dd>{{ session.me?.email }}</dd>
        </dl>

        <form @submit.prevent="submit">
          <SFormField
            :label="$t('identity.changeEmail.newEmail')"
            name="newEmail"
            :error="fieldErrors.newEmail"
            required
          >
            <SInput
              ref="emailRef"
              v-model="newEmail"
              type="email"
              autocomplete="email"
              :disabled="submitting || rateLimitSeconds > 0"
              :error="!!fieldErrors.newEmail"
              @blur="validateNewEmail"
            />
          </SFormField>

          <SFormField
            :label="$t('identity.changeEmail.password')"
            name="password"
            :error="fieldErrors.password"
            :help="$t('identity.changeEmail.passwordHelp')"
            required
          >
            <SInput
              v-model="password"
              type="password"
              autocomplete="current-password"
              :disabled="submitting || rateLimitSeconds > 0"
              :error="!!fieldErrors.password"
            />
          </SFormField>

          <SAlert
            v-if="serverError"
            variant="danger"
            class="form-alert"
          >
            {{ serverError }}
          </SAlert>

          <SButton
            type="submit"
            variant="primary"
            size="md"
            :loading="submitting"
            :disabled="submitting || rateLimitSeconds > 0"
            :aria-busy="submitting"
            class="form-submit"
          >
            {{ $t('identity.changeEmail.submit') }}
          </SButton>
        </form>
      </template>

      <div
        v-else
        class="done-content"
        aria-live="polite"
      >
        <EnvelopeIcon
          class="done-icon"
          aria-hidden="true"
        />
        <h2 class="done-title">
          {{ $t('identity.changeEmail.sentTitle') }}
        </h2>
        <p class="done-text">
          {{ $t('identity.changeEmail.sentDescription', { email: submittedEmail }) }}
        </p>
      </div>
    </SCard>
  </div>
</template>

<style scoped>
.form-card {
  max-width: 480px;
}

.current-email {
  margin: 0 0 20px;
}

.current-email dt {
  font-size: 0.75rem;
  color: var(--color-muted);
  margin-bottom: 2px;
}

.current-email dd {
  font-size: 0.875rem;
  color: var(--color-fg);
  margin: 0;
}

form {
  display: flex;
  flex-direction: column;
  gap: 16px;
}

.form-alert {
  margin: 0;
}

.form-submit {
  width: 100%;
}

.done-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 12px;
  padding: 16px 0;
}

.done-icon {
  width: 48px;
  height: 48px;
  color: var(--color-accent);
}

.done-title {
  font-size: 1.25rem;
  font-weight: 600;
  color: var(--color-fg);
  margin: 0;
}

.done-text {
  font-size: 0.875rem;
  color: var(--color-muted);
  margin: 0;
}

@media (max-width: 768px) {
  .form-card {
    max-width: none;
  }
}
</style>
