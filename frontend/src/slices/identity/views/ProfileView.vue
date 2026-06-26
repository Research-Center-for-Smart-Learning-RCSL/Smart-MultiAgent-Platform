<script setup lang="ts">
import { ref, onMounted, nextTick } from 'vue'
import { useI18n } from 'vue-i18n'
import { SPageHeader, SCard, SFormField, SInput, SButton, SAlert } from '@shared/ui'
import { authApi } from '../api/auth'
import { useSessionStore } from '../stores/session'
import { displayNameSchema, DISPLAY_NAME_MAX_LENGTH, validateField } from '../validation'

const { t } = useI18n()
const session = useSessionStore()

const displayName = ref('')
const serverError = ref<string | null>(null)
const saved = ref(false)
const submitting = ref(false)
const fieldErrors = ref<Record<string, string | undefined>>({})
const inputRef = ref<InstanceType<typeof SInput> | null>(null)

onMounted(async () => {
  displayName.value = session.me?.display_name ?? ''
  await nextTick()
  inputRef.value?.$el?.querySelector('input')?.focus()
})

function validateDisplayName(): boolean {
  return validateField(displayNameSchema, displayName.value, fieldErrors, 'displayName', t)
}

async function submit(): Promise<void> {
  serverError.value = null
  saved.value = false
  if (!validateDisplayName()) return

  submitting.value = true
  try {
    const trimmed = displayName.value.trim()
    const { data } = await authApi.updateProfile({ display_name: trimmed || null })
    // Reflect the server-normalised value (control chars stripped, re-trimmed).
    session.setMe(data)
    displayName.value = data.display_name ?? ''
    saved.value = true
  } catch {
    serverError.value = t('identity.errors.generic')
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div>
    <SPageHeader :title="$t('identity.profile.title')" />

    <SCard class="form-card">
      <dl class="current-email">
        <dt>{{ $t('identity.profile.emailLabel') }}</dt>
        <dd>{{ session.me?.email }}</dd>
      </dl>

      <form
        class="auth-form"
        @submit.prevent="submit"
      >
        <SFormField
          :label="$t('identity.profile.displayName')"
          name="displayName"
          :error="fieldErrors.displayName"
          :help="$t('identity.profile.displayNameHelp')"
        >
          <SInput
            ref="inputRef"
            v-model="displayName"
            type="text"
            autocomplete="nickname"
            :maxlength="DISPLAY_NAME_MAX_LENGTH"
            :disabled="submitting"
            :error="!!fieldErrors.displayName"
            @input="saved = false"
            @blur="validateDisplayName"
          />
        </SFormField>

        <SAlert
          v-if="saved"
          variant="success"
        >
          {{ $t('identity.profile.saved') }}
        </SAlert>

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
          :disabled="submitting"
          :aria-busy="submitting"
          class="form-submit"
        >
          {{ $t('identity.profile.submit') }}
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
