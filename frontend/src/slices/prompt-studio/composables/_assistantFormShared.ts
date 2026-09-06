// Shared plumbing between useConfigEditor (user/org singleton config) and
// usePresetEditor (id-addressed platform presets): both editors pin one of
// the actor's own chat-capable keys and resolve that key's model catalog the
// same way, and map the same backend error codes to the same toasts.
import { useQuery } from '@tanstack/vue-query'
import { computed, type ComputedRef } from 'vue'
import { useI18n } from 'vue-i18n'

import { useModelCatalog } from '@shared/composables'
import type { useToast } from '@shared/composables'
import { isProblemWithType } from '@shared/transport'
import { CAPABILITIES, keysApi, type ApiKey, type ApiKeyProvider } from '@slices/keys'

export function useChatKeyAndModelOptions(keyId: ComputedRef<string | null>) {
  const { t } = useI18n()
  const keysQuery = useQuery({
    queryKey: ['prompt-studio', 'my-keys'],
    queryFn: () => keysApi.list(),
  })
  const catalogQuery = useModelCatalog()

  const chatKeys = computed<ApiKey[]>(() =>
    (keysQuery.data.value ?? []).filter((k) =>
      CAPABILITIES[k.provider as ApiKeyProvider]?.includes('llm_chat'),
    ),
  )

  const keyOptions = computed(() =>
    chatKeys.value.map((k) => ({ value: k.id, label: `${k.name} (${k.masked_preview})` })),
  )

  const modelOptions = computed(() => {
    if (!keyId.value) return []
    const provider = chatKeys.value.find((k) => k.id === keyId.value)?.provider
    if (!provider) return []
    const entry = (catalogQuery.data.value?.chat ?? []).find((c) => c.provider === provider)
    if (!entry) return []
    return [
      { value: '', label: t('promptStudio.config.modelDefault', { model: entry.default }) },
      ...entry.models.map((m) => ({ value: m.model_id, label: m.model_id })),
    ]
  })

  return { keysQuery, catalogQuery, keyOptions, modelOptions }
}

export function toastForSaveError(err: unknown, toast: ReturnType<typeof useToast>, t: (k: string) => string) {
  if (isProblemWithType(err, 'prompt-studio/version-mismatch')) {
    toast.warning(t('promptStudio.config.conflict'))
  } else if (isProblemWithType(err, 'prompt-studio/key-not-owned')) {
    toast.error(t('promptStudio.config.keyNotOwned'))
  } else if (isProblemWithType(err, 'prompt-studio/key-capability')) {
    toast.error(t('promptStudio.config.keyCapability'))
  } else {
    toast.error(t('promptStudio.config.saveFailed'))
  }
}

export function toastForUploadError(
  err: unknown,
  toast: ReturnType<typeof useToast>,
  t: (k: string) => string,
) {
  if (isProblemWithType(err, 'prompt-studio/text-budget')) {
    toast.error(t('promptStudio.config.budgetError'))
  } else if (isProblemWithType(err, 'prompt-studio/file-format')) {
    toast.error(t('promptStudio.config.formatError'))
  } else if (isProblemWithType(err, 'prompt-studio/file-infected')) {
    toast.error(t('promptStudio.config.infectedError'))
  } else if (isProblemWithType(err, 'prompt-studio/config-not-found')) {
    toast.error(t('promptStudio.config.saveFirst'))
  } else {
    toast.error(t('promptStudio.config.uploadFailed'))
  }
}
