// Room-settings side of guest moderation: rotating the guest link ([R6.12]) and
// the banned-guests list ([R13.07a]). Matrix row 18 decides server-side; the
// caller passes whether to show these at all.

import { computed, ref, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useQuery, useQueryClient } from '@tanstack/vue-query'
import { useConfirmDialog, useToast } from '@shared/composables'
import { listGuestBans, rotateGuestLink, unbanGuest } from '../api'
import { convKeys } from '../queries'

export function useGuestLinkModeration(chatroomId: string, enabled: Ref<boolean>) {
  const { t } = useI18n()
  const { confirm } = useConfirmDialog()
  const toast = useToast()
  const qc = useQueryClient()

  const rotating = ref(false)

  /** Resolves to the new link, or null when declined or refused. */
  async function rotateLink(): Promise<string | null> {
    const ok = await confirm({
      title: t('conversation.settings.rotateLinkTitle'),
      message: t('conversation.settings.rotateLinkConfirm'),
      variant: 'warning',
      confirmLabel: t('conversation.settings.rotateLink'),
    })
    if (!ok) return null
    rotating.value = true
    try {
      const link = await rotateGuestLink(chatroomId)
      toast.success(t('conversation.settings.linkRotated'))
      return link.url
    } catch {
      toast.error(t('conversation.settings.rotateFailed'))
      return null
    } finally {
      rotating.value = false
    }
  }

  const bansQuery = useQuery({
    queryKey: convKeys.guestBans(chatroomId),
    queryFn: () => listGuestBans(chatroomId),
    enabled,
  })

  // Per ban, replaced rather than mutated so the template's reads re-run: two
  // lifts in flight must each keep their own button busy.
  const unbanning = ref<ReadonlySet<string>>(new Set())

  async function unban(banId: string): Promise<void> {
    if (unbanning.value.has(banId)) return
    unbanning.value = new Set([...unbanning.value, banId])
    try {
      await unbanGuest(chatroomId, banId)
      toast.success(t('conversation.settings.unbanned'))
    } catch {
      toast.error(t('conversation.settings.unbanFailed'))
    } finally {
      unbanning.value = new Set([...unbanning.value].filter((id) => id !== banId))
      void qc.invalidateQueries({ queryKey: convKeys.guestBans(chatroomId) })
    }
  }

  return {
    rotating: computed(() => rotating.value),
    rotateLink,
    bansQuery,
    isUnbanning: (banId: string) => unbanning.value.has(banId),
    unban,
  }
}
