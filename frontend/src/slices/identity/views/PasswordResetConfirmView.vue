<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { ExclamationTriangleIcon } from '@heroicons/vue/24/outline'
import { SFormField, SInput, SButton, SAlert } from '@shared/ui'
import { isProblemWithType } from '@shared/transport'
import { authApi } from '../api/auth'

const { t } = useI18n()
const router = useRouter()

const newPassword = ref('')
const confirmPassword = ref('')
const serverError = ref<string | null>(null)
const submitting = ref(false)
const tokenMissing = ref(false)
const passwordRef = ref<InstanceType<typeof SInput> | null>(null)

const fieldErrors = ref<{ newPassword?: string; confirmPassword?: string }>({})

let token: string | null = null

function validateNewPassword(): boolean {
  if (!newPassword.value) {
    fieldErrors.value.newPassword = t('identity.validation.passwordRequired')
    return false
  }
  if (newPassword.value.length < 10) {
    fieldErrors.value.newPassword = t('identity.validation.passwordMinLength')
    return false
  }
  fieldErrors.value.newPassword = undefined
  return true
}

function validateConfirmPassword(): boolean {
  if (!confirmPassword.value) {
    fieldErrors.value.confirmPassword = t('identity.validation.passwordRequired')
    return false
  }
  if (confirmPassword.value !== newPassword.value) {
    fieldErrors.value.confirmPassword = t('identity.validation.passwordMismatch')
    return false
  }
  fieldErrors.value.confirmPassword = undefined
  return true
}

onMounted(async () => {
  token = new URLSearchParams(window.location.hash.slice(1)).get('token')
  if (!token) {
    tokenMissing.value = true
    return
  }
  await nextTick()
  passwordRef.value?.$el?.querySelector('input')?.focus()
})

async function submit(): Promise<void> {
  serverError.value = null
  const pwValid = validateNewPassword()
  const confirmValid = validateConfirmPassword()
  if (!pwValid || !confirmValid) return
  if (!token) {
    tokenMissing.value = true
    return
  }

  submitting.value = true
  try {
    await authApi.resetPassword({ token, new_password: newPassword.value })
    router.push({ name: 'identity.login', query: { passwordReset: '1' } })
  } catch (e: unknown) {
    if (isProblemWithType(e, '/auth/token-invalid')) {
      serverError.value = t('identity.passwordReset.invalidToken')
    } else if (isProblemWithType(e, '/auth/token-expired')) {
      serverError.value = t('identity.passwordReset.expiredToken')
    } else if (isProblemWithType(e, '/auth/password-weak')) {
      serverError.value = t('identity.errors.weakPassword')
      newPassword.value = ''
      confirmPassword.value = ''
      await nextTick()
      passwordRef.value?.$el?.querySelector('input')?.focus()
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
    <template v-if="tokenMissing">
      <div class="token-error">
        <ExclamationTriangleIcon
          class="token-error-icon"
          aria-hidden="true"
        />
        <h1 class="auth-heading">
          {{ $t('identity.passwordReset.confirmTitle') }}
        </h1>
        <p class="token-error-text">
          {{ $t('identity.passwordReset.invalidLink') }}
        </p>
        <SButton
          variant="secondary"
          :to="{ name: 'identity.passwordResetRequest' }"
          as="router-link"
        >
          {{ $t('identity.passwordReset.requestNewLink') }}
        </SButton>
      </div>
    </template>

    <template v-else>
      <h1
        id="reset-confirm-heading"
        class="auth-heading"
      >
        {{ $t('identity.passwordReset.confirmTitle') }}
      </h1>

      <form
        aria-labelledby="reset-confirm-heading"
        @submit.prevent="submit"
      >
        <SFormField
          :label="$t('identity.passwordReset.newPassword')"
          name="newPassword"
          :error="fieldErrors.newPassword"
          :help="$t('identity.common.passwordPolicy')"
          required
        >
          <SInput
            ref="passwordRef"
            v-model="newPassword"
            type="password"
            autocomplete="new-password"
            :disabled="submitting"
            :error="!!fieldErrors.newPassword"
            @blur="validateNewPassword"
          />
        </SFormField>

        <SFormField
          :label="$t('identity.passwordReset.confirmPassword')"
          name="confirmPassword"
          :error="fieldErrors.confirmPassword"
          required
        >
          <SInput
            v-model="confirmPassword"
            type="password"
            autocomplete="new-password"
            :disabled="submitting"
            :error="!!fieldErrors.confirmPassword"
            @blur="validateConfirmPassword"
          />
        </SFormField>

        <SAlert
          v-if="serverError"
          variant="danger"
          class="form-alert"
        >
          {{ serverError }}
          <template
            v-if="serverError === t('identity.passwordReset.invalidToken') || serverError === t('identity.passwordReset.expiredToken')"
            #actions
          >
            <SButton
              variant="secondary"
              size="sm"
              :to="{ name: 'identity.passwordResetRequest' }"
              as="router-link"
            >
              {{ $t('identity.passwordReset.requestNewLink') }}
            </SButton>
          </template>
        </SAlert>

        <SButton
          type="submit"
          variant="primary"
          size="md"
          :loading="submitting"
          :disabled="submitting"
          :aria-busy="submitting"
          class="form-submit"
        >
          {{ $t('identity.passwordReset.confirmSubmit') }}
        </SButton>
      </form>
    </template>
  </div>
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

.form-alert {
  margin: 0;
}

.form-submit {
  width: 100%;
}

.token-error {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 16px;
}

.token-error-icon {
  width: 48px;
  height: 48px;
  color: var(--color-warning);
}

.token-error-text {
  font-size: 0.875rem;
  color: var(--color-muted);
  margin: 0;
}
</style>
