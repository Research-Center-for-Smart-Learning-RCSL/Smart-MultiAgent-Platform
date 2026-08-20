import { computed } from 'vue'
import { useI18n } from 'vue-i18n'

import { useConfirmDialog, useToast } from '@shared/composables'
import { isProblemWithType } from '@shared/transport'

import {
  useCreateTemplateMutation,
  useDeleteTemplateMutation,
  usePatchTemplateMutation,
  useTemplatesQuery,
} from '../queries'
import type { ConfigScopeRef } from '../types'

export function useTemplateEditor(scope: ConfigScopeRef) {
  const { t } = useI18n()
  const toast = useToast()
  const { confirm } = useConfirmDialog()

  const query = useTemplatesQuery(scope)
  const createMutation = useCreateTemplateMutation(scope)
  const patchMutation = usePatchTemplateMutation(scope)
  const deleteMutation = useDeleteTemplateMutation(scope)

  async function onCreate(value: { name: string; description: string; body: string }): Promise<void> {
    try {
      await createMutation.mutateAsync(value)
      toast.success(t('promptStudio.templates.created'))
    } catch (err) {
      if (isProblemWithType(err, 'prompt-studio/template-limit')) {
        toast.error(t('promptStudio.templates.limitError'))
      } else {
        toast.error(t('promptStudio.templates.saveFailed'))
      }
    }
  }

  async function onUpdate(value: {
    id: string
    version: number
    name: string
    description: string
    body: string
  }): Promise<void> {
    try {
      await patchMutation.mutateAsync({
        id: value.id,
        version: value.version,
        payload: { name: value.name, description: value.description, body: value.body },
      })
      toast.success(t('promptStudio.templates.updated'))
    } catch (err) {
      if (isProblemWithType(err, 'prompt-studio/version-mismatch')) {
        toast.warning(t('promptStudio.templates.conflict'))
      } else {
        toast.error(t('promptStudio.templates.saveFailed'))
      }
    }
  }

  async function onDelete(id: string): Promise<void> {
    const ok = await confirm({
      title: t('promptStudio.templates.deleteTitle'),
      message: t('promptStudio.templates.deleteBody'),
      variant: 'error',
    })
    if (!ok) return
    try {
      await deleteMutation.mutateAsync(id)
    } catch {
      toast.error(t('promptStudio.templates.saveFailed'))
    }
  }

  return {
    query,
    templates: computed(() => query.data.value ?? []),
    busy: computed(
      () => createMutation.isPending.value || patchMutation.isPending.value,
    ),
    onCreate,
    onUpdate,
    onDelete,
  }
}
