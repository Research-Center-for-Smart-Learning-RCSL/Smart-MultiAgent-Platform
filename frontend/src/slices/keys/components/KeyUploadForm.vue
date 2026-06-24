<script setup lang="ts">
import { computed } from 'vue'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { z } from 'zod'
import { useI18n } from 'vue-i18n'
import { LockClosedIcon } from '@heroicons/vue/24/outline'
import { SModal, SFormField, SSelect, SInput, SButton, SBadge, SAlert } from '@shared/ui'
import { CAPABILITIES, type ApiKeyProvider } from '../api/keys'

const CAP_LABELS: Record<string, string> = {
  llm_chat: 'llm',
  embedding: 'embed',
  rerank: 'rerank',
}

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'submit', payload: { provider: ApiKeyProvider; name: string; secret: string }): void
}>()

const { t } = useI18n()

const providerOptions = computed(() =>
  (Object.keys(CAPABILITIES) as ApiKeyProvider[]).map((p) => ({
    value: p,
    label: `${p.charAt(0).toUpperCase() + p.slice(1)} (${CAPABILITIES[p].map((c) => CAP_LABELS[c] ?? c).join(', ')})`,
  })),
)

const selectedCaps = computed(() =>
  provider.value ? CAPABILITIES[provider.value as ApiKeyProvider] ?? [] : [],
)

const schema = toTypedSchema(
  z.object({
    provider: z.enum(['claude', 'openai', 'gemini', 'voyage', 'cohere']),
    name: z.string().trim().min(1).max(200),
    secret: z.string().trim().min(1).max(4096),
  }),
)

const { handleSubmit, errors, defineField, resetForm } = useForm({
  validationSchema: schema,
  initialValues: { provider: 'openai' as ApiKeyProvider, name: '', secret: '' },
})
const [provider] = defineField('provider')
const [name] = defineField('name')
const [secret] = defineField('secret')

const onSubmit = handleSubmit((values) => {
  emit('submit', values as { provider: ApiKeyProvider; name: string; secret: string })
  resetForm()
})

function onClose() {
  resetForm()
  emit('close')
}
</script>

<template>
  <SModal
    :open="props.open"
    :title="t('keys.form.title')"
    size="md"
    @close="onClose"
  >
    <form
      id="key-upload-form"
      @submit.prevent="onSubmit"
    >
      <div class="flex flex-col gap-4">
        <SFormField
          :label="t('keys.form.provider')"
          name="provider"
          :error="errors.provider"
          required
        >
          <SSelect
            v-model="provider"
            :options="providerOptions"
            :placeholder="t('keys.form.providerPlaceholder')"
            :error="!!errors.provider"
            data-testid="key-provider"
          />
        </SFormField>

        <div
          v-if="selectedCaps.length > 0"
          class="flex items-center gap-1 -mt-2"
        >
          <SBadge
            v-for="c in selectedCaps"
            :key="c"
            variant="neutral"
            size="sm"
          >
            {{ CAP_LABELS[c] ?? c }}
          </SBadge>
        </div>

        <SFormField
          :label="t('keys.form.name')"
          name="name"
          :error="errors.name"
          required
        >
          <SInput
            v-model="name"
            :placeholder="t('keys.form.namePlaceholder')"
            :error="!!errors.name"
            data-testid="key-name"
          />
        </SFormField>

        <SFormField
          :label="t('keys.form.secret')"
          name="secret"
          :error="errors.secret"
          required
        >
          <SInput
            v-model="secret"
            type="password"
            autocomplete="new-password"
            :placeholder="t('keys.form.secretPlaceholder')"
            :error="!!errors.secret"
            data-testid="key-secret"
          />
        </SFormField>

        <SAlert variant="info">
          <template #default>
            <div class="flex items-start gap-2">
              <LockClosedIcon class="w-4 h-4 mt-0.5 shrink-0" />
              <div>
                <p class="font-medium text-sm">
                  {{ t('keys.form.securityTitle') }}
                </p>
                <p class="text-xs mt-0.5">
                  {{ t('keys.form.securityBody') }}
                </p>
              </div>
            </div>
          </template>
        </SAlert>
      </div>
    </form>

    <template #footer>
      <div class="flex justify-end gap-3">
        <SButton
          variant="secondary"
          @click="onClose"
        >
          {{ t('app.cancel') }}
        </SButton>
        <SButton
          variant="primary"
          type="submit"
          form="key-upload-form"
          data-testid="key-upload-submit"
        >
          {{ t('keys.form.submit') }}
        </SButton>
      </div>
    </template>
  </SModal>
</template>
