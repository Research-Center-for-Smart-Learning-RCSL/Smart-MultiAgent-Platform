<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useForm } from 'vee-validate'
import { toTypedSchema } from '@vee-validate/zod'
import { z } from 'zod'
import { useI18n } from 'vue-i18n'
import { LockClosedIcon } from '@heroicons/vue/24/outline'
import { SModal, SFormField, SSelect, SInput, SButton, SBadge, SAlert } from '@shared/ui'
import { INPUT_LIMITS } from '@shared/constants/inputLimits'
import { CAPABILITIES, type ApiKeyProvider, type OpenAICompatConfig } from '../api/keys'

const CAP_LABELS: Record<string, string> = {
  llm_chat: 'llm',
  embedding: 'embed',
  rerank: 'rerank',
}

const props = defineProps<{ open: boolean; loading?: boolean }>()
const emit = defineEmits<{
  (e: 'close'): void
  (
    e: 'submit',
    payload: { provider: ApiKeyProvider; name: string; secret: string; config?: OpenAICompatConfig },
  ): void
}>()

const { t } = useI18n()

const providerOptions = computed(() =>
  (Object.keys(CAPABILITIES) as ApiKeyProvider[]).map((p) => ({
    value: p,
    label:
      p === 'openai_compat'
        ? `${t('keys.providers.openai_compat')} (${CAPABILITIES[p].map((c) => CAP_LABELS[c] ?? c).join(', ')})`
        : `${p.charAt(0).toUpperCase() + p.slice(1)} (${CAPABILITIES[p].map((c) => CAP_LABELS[c] ?? c).join(', ')})`,
  })),
)

const selectedCaps = computed(() =>
  provider.value ? CAPABILITIES[provider.value as ApiKeyProvider] ?? [] : [],
)

const isOpenAICompat = computed(() => provider.value === 'openai_compat')

const schema = toTypedSchema(
  z.object({
    provider: z.enum(['claude', 'openai', 'gemini', 'voyage', 'cohere', 'openai_compat']),
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

const configBaseUrl = ref('')
const configLabel = ref('')
const configTimeout = ref<number | undefined>(undefined)
const configChatEnabled = ref(true)
const configEmbedEnabled = ref(true)

watch(
  () => props.open,
  (open) => {
    if (!open) {
      configBaseUrl.value = ''
      configLabel.value = ''
      configTimeout.value = undefined
      configChatEnabled.value = true
      configEmbedEnabled.value = true
    }
  },
)

const onSubmit = handleSubmit((values) => {
  const payload: {
    provider: ApiKeyProvider
    name: string
    secret: string
    config?: OpenAICompatConfig
  } = values as { provider: ApiKeyProvider; name: string; secret: string }
  if (values.provider === 'openai_compat') {
    const caps: Array<'llm_chat' | 'embedding'> = []
    if (configChatEnabled.value) caps.push('llm_chat')
    if (configEmbedEnabled.value) caps.push('embedding')
    payload.config = {
      base_url: configBaseUrl.value.trim(),
      ...(configLabel.value.trim() ? { label: configLabel.value.trim() } : {}),
      ...(configTimeout.value ? { timeout_s: configTimeout.value } : {}),
      ...(caps.length < 2 ? { capabilities: caps } : {}),
    }
  }
  emit('submit', payload)
  resetForm()
  configBaseUrl.value = ''
  configLabel.value = ''
  configTimeout.value = undefined
  configChatEnabled.value = true
  configEmbedEnabled.value = true
})

function onClose() {
  resetForm()
  configBaseUrl.value = ''
  configLabel.value = ''
  configTimeout.value = undefined
  configChatEnabled.value = true
  configEmbedEnabled.value = true
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
          :error="errors.provider ?? ''"
          required
        >
          <SSelect
            :model-value="provider ?? null"
            :options="providerOptions"
            :placeholder="t('keys.form.providerPlaceholder')"
            :error="!!errors.provider"
            data-testid="key-provider"
            @update:model-value="(v) => (provider = v as ApiKeyProvider)"
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
          :error="errors.name ?? ''"
          required
        >
          <SInput
            :model-value="name ?? ''"
            :maxlength="INPUT_LIMITS.KEY_NAME"
            :placeholder="t('keys.form.namePlaceholder')"
            :error="!!errors.name"
            data-testid="key-name"
            @update:model-value="(v) => (name = String(v))"
          />
        </SFormField>

        <SFormField
          :label="t('keys.form.secret')"
          name="secret"
          :error="errors.secret ?? ''"
          required
        >
          <SInput
            :model-value="secret ?? ''"
            type="password"
            autocomplete="new-password"
            :maxlength="INPUT_LIMITS.KEY_SECRET"
            :placeholder="t('keys.form.secretPlaceholder')"
            :error="!!errors.secret"
            data-testid="key-secret"
            @update:model-value="(v) => (secret = String(v))"
          />
        </SFormField>

        <template v-if="isOpenAICompat">
          <SFormField
            :label="t('keys.form.baseUrl')"
            name="config-base-url"
            required
          >
            <SInput
              v-model="configBaseUrl"
              :placeholder="t('keys.form.baseUrlPlaceholder')"
              data-testid="key-config-base-url"
            />
          </SFormField>

          <SFormField
            :label="t('keys.form.label')"
            name="config-label"
          >
            <SInput
              v-model="configLabel"
              :maxlength="100"
              :placeholder="t('keys.form.labelPlaceholder')"
              data-testid="key-config-label"
            />
          </SFormField>

          <SFormField
            :label="t('keys.form.timeout')"
            name="config-timeout"
          >
            <SInput
              :model-value="configTimeout ?? ''"
              type="number"
              :placeholder="t('keys.form.timeoutPlaceholder')"
              data-testid="key-config-timeout"
              @update:model-value="(v) => (configTimeout = v ? Number(v) : undefined)"
            />
          </SFormField>

          <SFormField
            :label="t('keys.form.capabilities')"
            name="config-capabilities"
          >
            <div class="flex gap-4">
              <label class="inline-flex items-center gap-1.5 text-sm">
                <input
                  v-model="configChatEnabled"
                  type="checkbox"
                  class="rounded"
                >
                {{ CAP_LABELS.llm_chat }}
              </label>
              <label class="inline-flex items-center gap-1.5 text-sm">
                <input
                  v-model="configEmbedEnabled"
                  type="checkbox"
                  class="rounded"
                >
                {{ CAP_LABELS.embedding }}
              </label>
            </div>
          </SFormField>
        </template>

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
          :loading="props.loading"
          :disabled="props.loading"
          data-testid="key-upload-submit"
        >
          {{ t('keys.form.submit') }}
        </SButton>
      </div>
    </template>
  </SModal>
</template>
