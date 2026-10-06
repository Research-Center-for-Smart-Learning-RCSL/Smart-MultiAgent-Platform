// Removing or banning an anonymous guest from the room ([R13.07a]). The server
// gates on matrix row 18; callers show the actions only to moderators.

import { useI18n } from 'vue-i18n'
import { useQueryClient } from '@tanstack/vue-query'
import { useConfirmDialog, useToast } from '@shared/composables'
import { removeGuest } from '../api'
import { convKeys } from '../queries'

export function useGuestModeration(chatroomId: string) {
  const { t } = useI18n()
  const { confirm } = useConfirmDialog()
  const toast = useToast()
  const qc = useQueryClient()

  async function moderateGuest(guestSessionId: string, name: string, ban: boolean): Promise<void> {
    const ok = await confirm({
      title: t(ban ? 'conversation.chatroom.banGuestTitle' : 'conversation.chatroom.removeGuestTitle'),
      message: t(ban ? 'conversation.chatroom.banGuestConfirm' : 'conversation.chatroom.removeGuestConfirm', {
        name,
      }),
      variant: 'warning',
      confirmLabel: t(ban ? 'conversation.chatroom.banGuest' : 'conversation.chatroom.removeGuest'),
    })
    if (!ok) return
    try {
      await removeGuest(chatroomId, guestSessionId, ban)
    } catch {
      toast.error(t('conversation.chatroom.removeGuestFailed'))
      return
    }
    toast.success(t(ban ? 'conversation.chatroom.guestBanned' : 'conversation.chatroom.guestRemoved', { name }))
    if (ban) void qc.invalidateQueries({ queryKey: convKeys.guestBans(chatroomId) })
  }

  return { moderateGuest }
}
