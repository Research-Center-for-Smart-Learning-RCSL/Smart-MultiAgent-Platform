import { computed, reactive, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { useToast } from '@shared/composables'

import {
  useConfigQuery,
  useDeleteFileMutation,
  useSaveConfigMutation,
  useUploadFileMutation,
} from '../queries'
import type { AssistantConfigPutInput, ConfigScopeRef } from '../types'
import { toastForSaveError, toastForUploadError, useChatKeyAndModelOptions } from './_assistantFormShared'

function blankValue(): AssistantConfigPutInput {
  return {
    persona_prompt: '',
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

  const form = reactive<AssistantConfigPutInput>(blankValue())
  const version = ref<number | null>(null)
  let baseline = JSON.stringify(blankValue())

  watch(
    () => configQuery.data.value,
    (env) => {
      const cfg = env?.config
      if (cfg) {
        Object.assign(form, {
          persona_prompt: cfg.persona_prompt,
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

  const { keyOptions, modelOptions } = useChatKeyAndModelOptions(
    computed(() => form.key_id),
  )

  const keyRevoked = computed(() => configQuery.data.value?.config?.key_revoked === true)
  const files = computed(() => configQuery.data.value?.config?.files ?? [])

  const saveMutation = useSaveConfigMutation(scope)
  const uploadMutation = useUploadFileMutation(scope)
  const deleteFileMutation = useDeleteFileMutation(scope)

  async function save(): Promise<void> {
    try {
      await saveMutation.mutateAsync({ version: version.value, payload: { ...form } })
      toast.success(t('promptStudio.config.saved'))
    } catch (err) {
      toastForSaveError(err, toast, t)
    }
  }

  async function uploadFile(file: File): Promise<void> {
    try {
      await uploadMutation.mutateAsync(file)
      toast.success(t('promptStudio.config.fileUploaded'))
    } catch (err) {
      toastForUploadError(err, toast, t)
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
