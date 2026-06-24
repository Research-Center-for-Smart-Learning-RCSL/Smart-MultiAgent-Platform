<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { SPageHeader, SCard, SFormField, SInput, SButton, SAlert } from '@shared/ui'
import { isProblemWithType } from '@shared/transport'
import { RateLimitError } from '@shared/errors'
import { useRateLimitCountdown } from '@shared/composables'
import { authApi } from '../api/auth'
import { useSessionStore } from '../stores/session'
import { emailSchema, validateField } from '../validation'

const { t } = useI18n()
const router = useRouter()
const session = useSessionStore()
const rateLimit = useRateLimitCountdown()

const newEmail = ref('')
const password = ref('')
const done = ref(false)
const submittedEmail = ref('')
const serverError = ref<string | null>(null)
const submitting = ref(false)
const emailRef = ref<InstanceType<typeof SInput> | null>(null)
const passwordRef = ref<InstanceType<typeof SInput> | null>(null)

const fieldErrors = ref<Record<string, string | undefined>>({})

function validateNewEmail(): boolean {
  const valid = validateField(emailSchema, newEmail.value, fieldErrors, 'newEmail')
  if (!valid) return false
  if (newEmail.value === session.me?.email) {
    fieldErrors.value.newEmail = 'identity.validation.emailSame'
    return false
  }
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

onMounted(async () => {
  await nextTick()
  emailRef.value?.$el?.querySelector('input')?.focus()
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
    session.clear()
    router.push({ name: 'identity.login' })
  } catch (e: unknown) {
    if (e instanceof RateLimitError) {
      const seconds = Math.ceil(e.retryAfterMs / 1000)
      serverError.value = t('identity.errors.rateLimit')
      rateLimit.start(seconds)
    } else if (isProblemWithType(e, '/auth/invalid-credentials')) {
      fieldErrors.value.password = t('identity.errors.invalidCredentials')
      password.value = ''
      await nextTick()
      passwordRef.value?.$el?.querySelector('input')?.focus()
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
      <dl class="current-email">
        <dt>{{ $t('identity.changeEmail.currentLabel') }}</dt>
        <dd>{{ session.me?.email }}</dd>
      </dl>

      <form
        class="auth-form"
        @submit.prevent="submit"
      >
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
            :disabled="submitting || rateLimit.active.value"
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
            ref="passwordRef"
            v-model="password"
            type="password"
            autocomplete="current-password"
            :disabled="submitting || rateLimit.active.value"
            :error="!!fieldErrors.password"
          />
        </SFormField>

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
          {{ $t('identity.changeEmail.submit') }}
        </SButton>
      </form>
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

.form-submit {
  width: 100%;
}

@media (max-width: 768px) {
  .form-card {
    max-width: none;
  }
}
</style>
