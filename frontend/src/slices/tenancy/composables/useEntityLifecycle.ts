import { useI18n } from 'vue-i18n'
import { useRouter } from 'vue-router'
import { useConfirmDialog, useToast } from '@shared/composables'
import { isProblemWithType } from '@shared/transport'
import type { QueryClient } from '@tanstack/vue-query'

interface EntityLifecycleOptions {
  entityName: () => string
  deleteTitle: () => string
  deleteBody: () => string
  deleteConfirmLabel: () => string
  deletedToast: () => string
  restoreTitle: () => string
  restoreBody: () => string
  restoreConfirmLabel: () => string
  restoredToast: () => string
  errorToast: () => string
  removeApi: (id: string) => Promise<unknown>
  restoreApi: (id: string) => Promise<unknown>
  queryKey: () => readonly unknown[]
  qc: QueryClient
  listRoute: string
}

export function useEntityLifecycle(options: EntityLifecycleOptions) {
  const { t } = useI18n()
  const router = useRouter()
  const { confirm, prompt } = useConfirmDialog()
  const toast = useToast()

  async function deleteEntity(id: string): Promise<void> {
    const name = await prompt({
      title: options.deleteTitle(),
      message: options.deleteBody(),
      variant: 'error',
      confirmLabel: options.deleteConfirmLabel(),
      inputPattern: new RegExp(`^${options.entityName().replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}$`),
      inputErrorMessage: options.deleteConfirmLabel(),
    })
    if (name === null) return
    try {
      await options.removeApi(id)
      toast.success(options.deletedToast())
      router.push({ name: options.listRoute })
    } catch (e: unknown) {
      if (isProblemWithType(e, '/tenancy/version-mismatch')) {
        toast.warning(t('tenancy.common.versionConflict'))
      } else {
        toast.error(options.errorToast())
      }
    }
  }

  async function restoreEntity(id: string): Promise<void> {
    const ok = await confirm({
      title: options.restoreTitle(),
      message: options.restoreBody(),
      variant: 'info',
      confirmLabel: options.restoreConfirmLabel(),
    })
    if (!ok) return
    try {
      await options.restoreApi(id)
      options.qc.invalidateQueries({ queryKey: options.queryKey() })
      toast.success(options.restoredToast())
    } catch (e: unknown) {
      if (isProblemWithType(e, '/tenancy/version-mismatch')) {
        toast.warning(t('tenancy.common.versionConflict'))
      } else {
        toast.error(options.errorToast())
      }
    }
  }

  function copyToClipboard(text: string): void {
    navigator.clipboard.writeText(text).catch(() => {
      toast.error(t('tenancy.common.loading'))
    })
  }

  return { deleteEntity, restoreEntity, copyToClipboard }
}
