<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { EnvelopeIcon } from '@heroicons/vue/24/outline'
import { SAuthCard, SFormField, SInput, SButton, SAlert } from '@shared/ui'
import { RateLimitError } from '@shared/errors'
import { authApi } from '../api/auth'
import { emailSchema, validateField, errorAttrs } from '../validation'

const { t } = useI18n()

const email = ref('')
const sent = ref(false)
const submitting = ref(false)
const serverError = ref<string | null>(null)
const isRateLimited = ref(false)
const emailRef = ref<InstanceType<typeof SInput> | null>(null)
const fieldErrors = ref<Record<string, string | undefined>>({})

function validateEmail(): boolean {
  return validateField(emailSchema, email.value, fieldErrors, 'email', t)
}

onMounted(async () => {
  await nextTick()
  emailRef.value?.$el?.querySelector('input')?.focus()
})

async function submit(): Promise<void> {
  serverError.value = null
  isRateLimited.value = false
  if (!validateEmail()) return

  submitting.value = true
  try {
    await authApi.requestPasswordReset(email.value)
    email.value = ''
    sent.value = true
  } catch (e: unknown) {
    if (e instanceof RateLimitError) {
      serverError.value = t('identity.errors.resetRateLimit')
      isRateLimited.value = true
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
    <template v-if="!sent">
      <h1
        id="reset-heading"
        class="card-heading"
      >
        {{ $t('identity.passwordReset.requestTitle') }}
      </h1>

      <p class="description">
        {{ $t('identity.passwordReset.requestDescription') }}
      </p>

      <form
        class="form-stack"
        aria-labelledby="reset-heading"
        @submit.prevent="submit"
      >
        <SFormField
          :label="$t('identity.passwordReset.email')"
          name="email"
          v-bind="errorAttrs(fieldErrors.email)"
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
          :variant="isRateLimited ? 'warning' : 'danger'"
          focus-on-mount
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
          class="submit-btn"
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
        <h1 class="card-heading">
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
        >
          {{ $t('identity.common.backToLogin') }}
        </SButton>
      </div>
    </template>

    <template
      v-if="!sent"
      #footer
    >
      <RouterLink :to="{ name: 'identity.login' }">
        {{ $t('identity.common.backToLogin') }}
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

.description {
  font-size: var(--font-size-sm);
  color: var(--color-muted);
  margin: 0 0 var(--space-6);
}

.sent-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  text-align: center;
  gap: var(--space-4);
}

.sent-icon {
  width: 48px;
  height: 48px;
  color: var(--color-accent);
}

.sent-text {
  font-size: var(--font-size-sm);
  color: var(--color-muted);
  margin: 0;
}
</style>
