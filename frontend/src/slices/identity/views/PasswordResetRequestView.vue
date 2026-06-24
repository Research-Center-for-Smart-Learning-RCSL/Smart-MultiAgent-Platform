<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { EnvelopeIcon } from '@heroicons/vue/24/outline'
import { SFormField, SInput, SButton, SAlert } from '@shared/ui'
import { RateLimitError } from '@shared/errors'
import { authApi } from '../api/auth'

const { t } = useI18n()

const email = ref('')
const sent = ref(false)
const submitting = ref(false)
const serverError = ref<string | null>(null)
const emailRef = ref<InstanceType<typeof SInput> | null>(null)
const fieldErrors = ref<{ email?: string }>({})

function validateEmail(): boolean {
  if (!email.value.trim()) {
    fieldErrors.value.email = t('identity.validation.emailRequired')
    return false
  }
  const emailPattern = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
  if (!emailPattern.test(email.value)) {
    fieldErrors.value.email = t('identity.validation.emailFormat')
    return false
  }
  fieldErrors.value.email = undefined
  return true
}

onMounted(async () => {
  await nextTick()
  emailRef.value?.$el?.querySelector('input')?.focus()
})

async function submit(): Promise<void> {
  serverError.value = null
  if (!validateEmail()) return

  submitting.value = true
  try {
    await authApi.requestPasswordReset(email.value)
    email.value = ''
    sent.value = true
  } catch (e: unknown) {
    if (e instanceof RateLimitError) {
      serverError.value = t('identity.errors.resetRateLimit')
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
    <template v-if="!sent">
      <h1
        id="reset-heading"
        class="auth-heading"
      >
        {{ $t('identity.passwordReset.requestTitle') }}
      </h1>

      <p class="description">
        {{ $t('identity.passwordReset.requestDescription') }}
      </p>

      <form
        aria-labelledby="reset-heading"
        @submit.prevent="submit"
      >
        <SFormField
          :label="$t('identity.passwordReset.email')"
          name="email"
          :error="fieldErrors.email"
          required
        >
          <SInput
            ref="emailRef"
            v-model="email"
            type="email"
            autocomplete="email"
            :disabled="submitting"
            :error="!!fieldErrors.email"
            @blur="validateEmail"
          />
        </SFormField>

        <SAlert
          v-if="serverError"
          :variant="serverError === t('identity.errors.resetRateLimit') ? 'warning' : 'danger'"
          class="form-alert"
        >
          {{ serverError }}
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
          {{ $t('identity.passwordReset.requestSubmit') }}
        </SButton>
      </form>
    </template>

    <template v-else>
      <div
        class="sent-content"
        aria-live="polite"
      >
        <h1 class="auth-heading">
          {{ $t('identity.passwordReset.sentTitle') }}
        </h1>
        <EnvelopeIcon
          class="sent-icon"
          aria-hidden="true"
        />
        <p class="sent-text">
          {{ $t('identity.passwordReset.sentDescription') }}
        </p>
        <SButton
          variant="primary"
          :to="{ name: 'identity.login' }"
          as="router-link"
          class="form-submit"
        >
          {{ $t('identity.common.backToLogin') }}
        </SButton>
      </div>
    </template>
  </div>

  <p
    v-if="!sent"
    class="auth-footer"
  >
    <RouterLink :to="{ name: 'identity.login' }">
      {{ $t('identity.common.backToLogin') }}
    </RouterLink>
  </p>
</template>

<style scoped>
.auth-heading {
  font-size: 1.5rem;
  font-weight: 600;
  color: var(--color-fg);
  margin: 0 0 8px;
}

.description {
  font-size: 0.875rem;
  color: var(--color-muted);
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

.sent-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: 16px;
}

.sent-icon {
  width: 48px;
  height: 48px;
  color: var(--color-accent);
}

.sent-text {
  font-size: 0.875rem;
  color: var(--color-muted);
  margin: 0;
}
</style>
