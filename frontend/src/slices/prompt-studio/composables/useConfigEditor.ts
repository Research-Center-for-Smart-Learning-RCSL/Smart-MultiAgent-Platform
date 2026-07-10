import { useQuery } from '@tanstack/vue-query'
import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { useToast } from '@shared/composables'
import { isProblemWithType } from '@shared/transport'
import { CAPABILITIES, keysApi, type ApiKey, type ApiKeyProvider } from '@slices/keys'

import { promptStudioApi } from '../api'
import {
  useConfigQuery,
  useDeleteFileMutation,
  useSaveConfigMutation,
  useUploadFileMutation,
} from '../queries'
import type { AssistantConfigPutInput, ConfigScopeRef } from '../types'

function blankValue(): AssistantConfigPutInput {
  return {
    system_prompt: '',
    key_id: null,
    model_id: null,
    daily_request_limit_per_user: 50,
    enabled: false,
    hide_platform_templates: false,
  }
}

/** Wires config data + the user's chat-capable keys + model catalog + save. */
export function useConfigEditor(scope: ConfigScopeRef) {
  const { t } = useI18n()
  const toast = useToast()

  const configQuery = useConfigQuery(scope)
  const keysQuery = useQuery({
    queryKey: ['prompt-studio', 'my-keys'],
    queryFn: () => keysApi.list(),
  })
  const catalogQuery = useQuery({
    queryKey: ['prompt-studio', 'model-catalog'],
    queryFn: async () => (await promptStudioApi.getModelCatalog()).data,
    staleTime: Infinity,
  })

  const form = reactive<AssistantConfigPutInput>(blankValue())
  const version = ref<number | null>(null)
  let baseline = JSON.stringify(blankValue())

  watch(
    () => configQuery.data.value,
    (env) => {
      const cfg = env?.config
      if (cfg) {
        Object.assign(form, {
          system_prompt: cfg.system_prompt,
          key_id: cfg.key_id,
          model_id: cfg.model_id,
          daily_request_limit_per_user: cfg.daily_request_limit_per_user,
          enabled: cfg.enabled,
          hide_platform_templates: cfg.hide_platform_templates,
        })
        version.value = cfg.version
      } else {
        Object.assign(form, blankValue())
        version.value = null
      }
      baseline = JSON.stringify(form)
    },
    { immediate: true },
  )

  const dirty = computed(() => JSON.stringify(form) !== baseline)

  const chatKeys = computed<ApiKey[]>(() =>
    (keysQuery.data.value ?? []).filter((k) =>
      CAPABILITIES[k.provider as ApiKeyProvider]?.includes('llm_chat'),
    ),
  )

  const keyOptions = computed(() =>
    chatKeys.value.map((k) => ({ value: k.id, label: `${k.name} (${k.masked_preview})` })),
  )

  const keyRevoked = computed(() => configQuery.data.value?.config?.key_revoked === true)
  const files = computed(() => configQuery.data.value?.config?.files ?? [])

  const modelOptions = computed(() => {
    const keyId = form.key_id
    if (!keyId) return []
    const provider = chatKeys.value.find((k) => k.id === keyId)?.provider
    if (!provider) return []
    const entry = (catalogQuery.data.value?.chat ?? []).find((c) => c.provider === provider)
    if (!entry) return []
    return [
      { value: '', label: t('promptStudio.config.modelDefault', { model: entry.default }) },
      ...entry.models.map((m) => ({ value: m, label: m })),
    ]
  })

  const saveMutation = useSaveConfigMutation(scope)
  const uploadMutation = useUploadFileMutation(scope)
  const deleteFileMutation = useDeleteFileMutation(scope)

  async function save(): Promise<void> {
    try {
      await saveMutation.mutateAsync({ version: version.value, payload: { ...form } })
      toast.success(t('promptStudio.config.saved'))
    } catch (err) {
      if (isProblemWithType(err, 'prompt-studio/version-mismatch')) {
        toast.error(t('promptStudio.config.conflict'))
      } else if (isProblemWithType(err, 'prompt-studio/key-not-owned')) {
        toast.error(t('promptStudio.config.keyNotOwned'))
      } else if (isProblemWithType(err, 'prompt-studio/key-capability')) {
        toast.error(t('promptStudio.config.keyCapability'))
      } else {
        toast.error(t('promptStudio.config.saveFailed'))
      }
    }
  }

  async function uploadFile(file: File): Promise<void> {
    try {
      await uploadMutation.mutateAsync(file)
      toast.success(t('promptStudio.config.fileUploaded'))
    } catch (err) {
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
  }

  async function deleteFile(fileId: string): Promise<void> {
    try {
      await deleteFileMutation.mutateAsync(fileId)
    } catch {
      toast.error(t('promptStudio.config.uploadFailed'))
    }
  }

  return {
    configQuery,
    form,
    dirty,
    keyOptions,
    modelOptions,
    keyRevoked,
    files,
    saving: computed(() => saveMutation.isPending.value),
    uploading: computed(() => uploadMutation.isPending.value),
    save,
    uploadFile,
    deleteFile,
  }
}
