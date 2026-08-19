import { computed, type MaybeRefOrGetter, reactive, ref, toValue, watch } from 'vue'
import { useI18n } from 'vue-i18n'

import { useConfirmDialog, useToast } from '@shared/composables'
import { ApiError } from '@shared/errors'
import { isProblemWithType } from '@shared/transport'

import { parseList } from '../lib/parseList'
import {
  useDeleteSkillMutation,
  usePatchSkillMutation,
  useRestoreSkillMutation,
  useSkillQuery,
} from '../queries'
import type { SkillScopeRef } from '../types'

interface EditForm {
  description: string
  body: string
  requiresText: string
  allowedToolsText: string
}

function blank(): EditForm {
  return { description: '', body: '', requiresText: '', allowedToolsText: '' }
}

/**
 * Detail-editor state for one selected skill. Hydration is guarded on the loaded skill's
 * id so a background refetch (e.g. the invalidation after a save, or the version refresh
 * a 412 triggers) refreshes `version` without discarding unsaved edits — only a genuine
 * selection change re-seeds the form. This is what lets the 412 path recover the current
 * version and let the next save through, instead of prompt-studio's permanent conflict
 * loop (§7).
 */
export function useSkillEditor(scope: SkillScopeRef, skillId: MaybeRefOrGetter<string | null>) {
  const { t } = useI18n()
  const toast = useToast()
  const { confirm } = useConfirmDialog()

  const skillQuery = useSkillQuery(scope, skillId)
  const patchMutation = usePatchSkillMutation(scope)
  const deleteMutation = useDeleteSkillMutation(scope)
  const restoreMutation = useRestoreSkillMutation(scope)

  const form = reactive<EditForm>(blank())
  const version = ref<number | null>(null)
  const loadedId = ref<string | null>(null)
  const baseline = ref<string>(JSON.stringify(blank()))

  function serialize(): string {
    return JSON.stringify({ ...form })
  }

  watch(
    () => skillQuery.data.value,
    (skill) => {
      if (!skill) return
      // Always track the latest server version so a re-save after a conflict uses it.
      version.value = skill.version
      if (loadedId.value === skill.id) return
      // Selection changed (or first load): re-seed the form from the server state.
      form.description = skill.description
      form.body = skill.body
      form.requiresText = skill.requires.join('\n')
      form.allowedToolsText = skill.allowed_tools.join('\n')
      baseline.value = serialize()
      loadedId.value = skill.id
    },
    { immediate: true },
  )

  const dirty = computed(() => serialize() !== baseline.value)
  const saving = computed(() => patchMutation.isPending.value)
  const deleted = computed(() => !!skillQuery.data.value?.deleted_at)

  async function save(): Promise<void> {
    const id = toValue(skillId)
    if (!id) return
    try {
      await patchMutation.mutateAsync({
        skillId: id,
        version: version.value,
        body: {
          description: form.description,
          body: form.body,
          requires: parseList(form.requiresText),
          allowed_tools: parseList(form.allowedToolsText),
        },
      })
      baseline.value = serialize()
      toast.success(t('skills.editor.saved'))
    } catch (err) {
      if (isProblemWithType(err, 'skills/version-mismatch')) {
        // The 412 body carries the live version (error_mapping `_extras`); adopt it so the
        // user's kept edits save on the next click.
        if (err instanceof ApiError) {
          const current = Number(err.extra.current_version)
          if (Number.isFinite(current)) version.value = current
        }
        toast.warning(t('skills.editor.conflict'))
      } else if (isProblemWithType(err, 'skills/index-budget-exceeded')) {
        toast.error(t('skills.editor.indexBudget'))
      } else if (isProblemWithType(err, 'skills/requires-tool-missing')) {
        toast.error(t('skills.editor.requiresMissing'))
      } else {
        toast.error(t('skills.editor.saveFailed'))
      }
    }
  }

  async function remove(): Promise<boolean> {
    const id = toValue(skillId)
    if (!id) return false
    const ok = await confirm({
      title: t('skills.editor.deleteTitle'),
      message: t('skills.editor.deleteBody'),
      variant: 'error',
    })
    if (!ok) return false
    try {
      await deleteMutation.mutateAsync({ skillId: id, version: version.value })
      toast.success(t('skills.editor.deleted'))
      return true
    } catch {
      toast.error(t('skills.editor.deleteFailed'))
      return false
    }
  }

  async function restore(): Promise<void> {
    const id = toValue(skillId)
    if (!id) return
    try {
      await restoreMutation.mutateAsync(id)
      toast.success(t('skills.editor.restored'))
    } catch (err) {
      if (isProblemWithType(err, 'skills/restore-conflict')) {
        toast.warning(t('skills.editor.restoreConflict'))
      } else {
        toast.error(t('skills.editor.restoreFailed'))
      }
    }
  }

  return { skillQuery, form, version, dirty, saving, deleted, save, remove, restore }
}
