import { computed, reactive, ref, watch, toValue, type MaybeRefOrGetter } from 'vue'
import { useI18n } from 'vue-i18n'

import { useToast } from '@shared/composables'

import {
  useCreatePresetMutation,
  useDeletePresetFileMutation,
  usePresetsQuery,
  useUpdatePresetMutation,
  useUploadPresetFileMutation,
} from '../queries'
import type { AssistantConfigPresetInput } from '../types'
import { toastForSaveError, toastForUploadError, useChatKeyAndModelOptions } from './_assistantFormShared'

function blankValue(): AssistantConfigPresetInput {
  return {
    name: '',
    description: '',
    persona_prompt: '',
    system_prompt: '',
    key_id: null,
    model_id: null,
    daily_request_limit_per_user: 50,
    enabled: false,
  }
}

/**
 * Wires one platform preset's data + save/upload, addressed by id rather than
 * by scope (there is no singleton platform config any more, R29.16).
 *
 * `presetIdSource` is a ref/getter, not a plain value: the create and edit
 * routes share one component (AdminPromptPresetEditView), so Vue Router
 * reuses the instance across a create -> edit redirect instead of remounting
 * it -- a plain captured id would go stale the moment `save()` navigates from
 * `/presets/new` to `/presets/{createdId}`.
 *
 * `presetId === null` is create mode -- files cannot be attached until the
 * first save returns an id, which the caller (the edit view) then navigates to.
 */
export function usePresetEditor(presetIdSource: MaybeRefOrGetter<string | null>) {
  const { t } = useI18n()
  const toast = useToast()

  const presetId = computed(() => toValue(presetIdSource))
  const isNew = computed(() => presetId.value === null)

  const presetsQuery = usePresetsQuery()
  const preset = computed(() => presetsQuery.data.value?.find((p) => p.id === presetId.value) ?? null)

  const form = reactive<AssistantConfigPresetInput>(blankValue())
  const version = ref<number | null>(null)
  let baseline = JSON.stringify(blankValue())

  watch(
    [preset, isNew],
    ([p, creating]) => {
      if (p) {
        Object.assign(form, {
          name: p.name,
          description: p.description,
          persona_prompt: p.persona_prompt,
          system_prompt: p.system_prompt,
          key_id: p.key_id,
          model_id: p.model_id,
          daily_request_limit_per_user: p.daily_request_limit_per_user,
          enabled: p.enabled,
        })
        version.value = p.version
        baseline = JSON.stringify(form)
      } else if (creating) {
        // Reached by navigating from an edit page back to /presets/new
        // without a remount -- reset to a blank slate for the new preset.
        Object.assign(form, blankValue())
        version.value = null
        baseline = JSON.stringify(form)
      }
      // else: a real preset id whose row hasn't loaded yet -- keep the
      // current form rather than flashing it blank while the list fetches.
    },
    { immediate: true },
  )

  const dirty = computed(() => JSON.stringify(form) !== baseline)

  const { keyOptions, modelOptions } = useChatKeyAndModelOptions(computed(() => form.key_id))

  const keyRevoked = computed(() => preset.value?.key_revoked === true)
  const files = computed(() => preset.value?.files ?? [])

  const createMutation = useCreatePresetMutation()
  const updateMutation = useUpdatePresetMutation()
  const uploadMutation = useUploadPresetFileMutation()
  const deleteFileMutation = useDeletePresetFileMutation()

  /** Returns the created preset's id on a successful create; undefined otherwise. */
  async function save(): Promise<string | undefined> {
    try {
      if (presetId.value === null) {
        const created = await createMutation.mutateAsync({ ...form })
        toast.success(t('promptStudio.config.saved'))
        return created.id
      }
      const id = presetId.value
      await updateMutation.mutateAsync({ id, version: version.value ?? 0, payload: { ...form } })
      toast.success(t('promptStudio.config.saved'))
      return undefined
    } catch (err) {
      toastForSaveError(err, toast, t)
      return undefined
    }
  }

  async function uploadFile(file: File): Promise<void> {
    const id = presetId.value
    if (id === null) {
      toast.error(t('promptStudio.config.saveFirst'))
      return
    }
    try {
      await uploadMutation.mutateAsync({ id, file })
      toast.success(t('promptStudio.config.fileUploaded'))
    } catch (err) {
      toastForUploadError(err, toast, t)
    }
  }

  async function deleteFile(fileId: string): Promise<void> {
    const id = presetId.value
    if (id === null) return
    try {
      await deleteFileMutation.mutateAsync({ id, fileId })
    } catch {
      toast.error(t('promptStudio.config.uploadFailed'))
    }
  }

  return {
    presetsQuery,
    preset,
    form,
    dirty,
    keyOptions,
    modelOptions,
    keyRevoked,
    files,
    isNew,
    saving: computed(() => createMutation.isPending.value || updateMutation.isPending.value),
    uploading: computed(() => uploadMutation.isPending.value),
    save,
    uploadFile,
    deleteFile,
  }
}
