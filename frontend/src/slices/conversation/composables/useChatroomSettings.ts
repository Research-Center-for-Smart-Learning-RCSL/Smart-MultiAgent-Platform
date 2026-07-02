// Composable: chatroom CRUD (load, save, delete) and form state.
// Extracted from ChatroomSettingsView.vue (H16 SoC fix).

import { useQueryClient } from '@tanstack/vue-query'
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'

import { useConfirmDialog, useToast } from '@shared/composables'
import { useI18n } from 'vue-i18n'
import { ApiError } from '@shared/errors'
import {
  deleteChatroom,
  getChatroom,
  patchChatroom,
} from '../api'
import type { Chatroom } from '../types'

export function useChatroomSettings(chatroomId: string) {
  const { t } = useI18n()
  const toast = useToast()
  const { confirm } = useConfirmDialog()
  const router = useRouter()
  const qc = useQueryClient()

  const name = ref('')
  const flags = reactive({
    allow_org_members: false,
    allow_project_members: true,
    allow_project_owners_only: false,
    allow_guest_links: false,
    // R28.09 — creator-only; the server rejects a non-creator patch of this
    // field, so the UI only exposes the toggle to the creator.
    disclose_observers: true,
  })
  const room = ref<Chatroom | null>(null)

  const loading = ref(true)
  const loadError = ref(false)
  const saving = ref(false)
  const saveError = ref<string | null>(null)

  /** Copy a chatroom into the form fields. */
  function applyRoom(found: Chatroom): void {
    room.value = found
    name.value = found.name
    flags.allow_org_members = found.allow_org_members
    flags.allow_project_members = found.allow_project_members
    flags.allow_project_owners_only = found.allow_project_owners_only
    flags.allow_guest_links = found.allow_guest_links
    flags.disclose_observers = found.disclose_observers
  }

  /** Find this chatroom in any cached `['conversation','chatrooms']` list. */
  function findInCache(): Chatroom | null {
    const caches = qc.getQueriesData<Chatroom[]>({
      queryKey: ['conversation', 'chatrooms'],
    })
    for (const [, data] of caches) {
      const found = data?.find((r) => r.id === chatroomId)
      if (found) return found
    }
    return null
  }

  async function loadRoom(): Promise<void> {
    loading.value = true
    loadError.value = false
    const cached = findInCache()
    if (cached) {
      applyRoom(cached)
      loading.value = false
      return
    }
    try {
      applyRoom(await getChatroom(chatroomId))
    } catch {
      loadError.value = true
    } finally {
      loading.value = false
    }
  }

  async function onSave(): Promise<void> {
    if (!room.value || saving.value) return
    saving.value = true
    saveError.value = null
    try {
      // Deliberately omit `disclose_observers` — it is creator-only on the
      // server (R28.09). Including it in every generic save would 403 a
      // non-creator moderator editing the other access flags. Disclosure has
      // its own save path (`saveDisclosure`).
      const { disclose_observers: _omit, ...patchFlags } = flags
      applyRoom(
        await patchChatroom(chatroomId, room.value.version, {
          name: name.value,
          ...patchFlags,
        }),
      )
      await qc.invalidateQueries({ queryKey: ['conversation', 'chatrooms'] })
      toast.success(t('conversation.settings.saved'))
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        saveError.value = 'conversation.settings.versionConflict'
        try {
          room.value = await getChatroom(chatroomId)
        } catch {
          /* keep the form as-is; the inline error already explains the retry */
        }
      } else {
        saveError.value = 'conversation.settings.saveFailed'
      }
    } finally {
      saving.value = false
    }
  }

  /** Creator-only patch of just `disclose_observers` (R28.09). Kept separate
   *  from `onSave` so the field is never sent by a non-creator. */
  async function saveDisclosure(value: boolean): Promise<void> {
    if (!room.value || saving.value) return
    saving.value = true
    saveError.value = null
    flags.disclose_observers = value
    try {
      applyRoom(
        await patchChatroom(chatroomId, room.value.version, {
          disclose_observers: value,
        }),
      )
      await qc.invalidateQueries({ queryKey: ['conversation', 'chatrooms'] })
      toast.success(t('conversation.settings.saved'))
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) {
        saveError.value = 'conversation.settings.versionConflict'
        try {
          room.value = await getChatroom(chatroomId)
        } catch {
          /* keep the form as-is */
        }
      } else {
        saveError.value = 'conversation.settings.saveFailed'
      }
    } finally {
      saving.value = false
    }
  }

  async function onDelete(): Promise<void> {
    const ok = await confirm({
      title: t('conversation.settings.deleteConfirmTitle'),
      message: t('conversation.settings.deleteConfirm'),
      confirmLabel: t('conversation.settings.delete'),
      cancelLabel: t('app.cancel'),
      variant: 'warning',
    })
    if (!ok) return
    try {
      await deleteChatroom(chatroomId)
    } catch {
      toast.error(t('conversation.settings.deleteFailed'))
      return
    }
    await qc.invalidateQueries({ queryKey: ['conversation', 'chatrooms'] })
    router.back()
  }

  return {
    name,
    flags,
    room,
    loading,
    loadError,
    saving,
    saveError,
    applyRoom,
    loadRoom,
    onSave,
    saveDisclosure,
    onDelete,
  }
}
