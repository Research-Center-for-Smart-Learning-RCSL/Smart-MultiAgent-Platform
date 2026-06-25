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
import { passwordSchema, validateField, validatePasswordMatch } from '../validation'

const { t } = useI18n()
const router = useRouter()
const session = useSessionStore()
const rateLimit = useRateLimitCountdown()

const currentPassword = ref('')
const newPassword = ref('')
const confirmPassword = ref('')
const serverError = ref<string | null>(null)
const submitting = ref(false)
const currentRef = ref<InstanceType<typeof SInput> | null>(null)

const fieldErrors = ref<Record<string, string | undefined>>({})

function validateCurrentPassword(): boolean {
  if (!currentPassword.value) {
    fieldErrors.value.currentPassword = t('identity.validation.passwordRequired')
    return false
  }
  fieldErrors.value.currentPassword = undefined
  return true
}

function validateNewPassword(): boolean {
  const valid = validateField(passwordSchema, newPassword.value, fieldErrors, 'newPassword', t)
  if (!valid) return false
  if (newPassword.value === currentPassword.value) {
    fieldErrors.value.newPassword = t('identity.validation.passwordSame')
    return false
  }
  return true
}

function validateConfirmPassword(): boolean {
  return validatePasswordMatch(
    newPassword.value, confirmPassword.value, fieldErrors, 'confirmPassword', t,
  )
}

onMounted(async () => {
  await nextTick()
  currentRef.value?.$el?.querySelector('input')?.focus()
})

async function submit(): Promise<void> {
  serverError.value = null
  const curValid = validateCurrentPassword()
  const newValid = validateNewPassword()
  const confirmValid = validateConfirmPassword()
  if (!curValid || !newValid || !confirmValid) return

  submitting.value = true
  try {
    await authApi.changePassword({
      current_password: currentPassword.value,
      new_password: newPassword.value,
    })
    session.clear()
    router.push({ name: 'identity.login', query: { passwordChanged: '1' } })
  } catch (e: unknown) {
    if (e instanceof RateLimitError) {
      const seconds = Math.ceil(e.retryAfterMs / 1000)
      serverError.value = t('identity.errors.rateLimit')
      rateLimit.start(seconds)
    } else if (isProblemWithType(e, '/auth/invalid-credentials')) {
      fieldErrors.value.currentPassword = t('identity.errors.invalidCredentials')
      currentPassword.value = ''
      await nextTick()
      currentRef.value?.$el?.querySelector('input')?.focus()
    } else if (isProblemWithType(e, '/auth/password-weak')) {
      serverError.value = t('identity.errors.weakPassword')
      newPassword.value = ''
      confirmPassword.value = ''
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
    <SPageHeader :title="$t('identity.changePassword.title')" />

    <SCard class="form-card">
      <form
        class="auth-form"
        :aria-describedby="'change-pw-warning'"
        @submit.prevent="submit"
      >
        <SFormField
          :label="$t('identity.changePassword.current')"
          name="currentPassword"
          :error="fieldErrors.currentPassword"
          required
        >
          <SInput
            ref="currentRef"
            v-model="currentPassword"
            type="password"
            autocomplete="current-password"
            :disabled="submitting || rateLimit.active.value"
            :error="!!fieldErrors.currentPassword"
          />
        </SFormField>

        <SFormField
          :label="$t('identity.changePassword.new')"
          name="newPassword"
          :error="fieldErrors.newPassword"
          :help="$t('identity.common.passwordPolicy')"
          required
        >
          <SInput
            v-model="newPassword"
            type="password"
            autocomplete="new-password"
            :disabled="submitting || rateLimit.active.value"
            :error="!!fieldErrors.newPassword"
            @blur="validateNewPassword"
          />
        </SFormField>

        <SFormField
          :label="$t('identity.changePassword.confirm')"
          name="confirmPassword"
          :error="fieldErrors.confirmPassword"
          required
        >
          <SInput
            v-model="confirmPassword"
            type="password"
            autocomplete="new-password"
            :disabled="submitting || rateLimit.active.value"
            :error="!!fieldErrors.confirmPassword"
            @blur="validateConfirmPassword"
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
          {{ $t('identity.changePassword.submit') }}
        </SButton>
      </form>
    </SCard>

    <p
      id="change-pw-warning"
      class="warning-text"
    >
      {{ $t('identity.changePassword.warning') }}
    </p>
  </div>
</template>

<style scoped>
.form-card {
  max-width: 480px;
}

.form-submit {
  width: 100%;
}

.warning-text {
  max-width: 480px;
  font-size: 0.875rem;
  color: var(--color-muted);
  margin-top: 16px;
}

@media (max-width: 768px) {
  .form-card,
  .warning-text {
    max-width: none;
  }
}
</style>
