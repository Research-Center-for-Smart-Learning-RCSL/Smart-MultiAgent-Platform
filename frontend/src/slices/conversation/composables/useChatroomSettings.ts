// Composable: chatroom CRUD (load, save, delete) and form state.
// Extracted from ChatroomSettingsView.vue (H16 SoC fix).

import { useQueryClient } from '@tanstack/vue-query'
import { computed, ref } from 'vue'
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

/** The access flags `setFlag` may patch. `disclose_observers` is excluded by
 *  type: it is creator-only on the server (R28.09) and has its own path. */
export type AccessFlag =
  | 'allow_org_members'
  | 'allow_project_members'
  | 'allow_project_owners_only'
  | 'allow_guest_links'
  | 'allow_member_groups'

export function useChatroomSettings(chatroomId: string) {
  const { t } = useI18n()
  const toast = useToast()
  const { confirm } = useConfirmDialog()
  const router = useRouter()
  const qc = useQueryClient()

  const name = ref('')
  const room = ref<Chatroom | null>(null)
  // The name the form last took from the server. `name` diverging from it is
  // the definition of "the user is typing", which a background revalidation
  // must not overwrite.
  let syncedName = ''

  // F-7: a projection of server state, never a mirror of it. `SToggle` is
  // fully controlled, so a toggle reading this cannot display a value the
  // server did not accept — there is no second copy to forget to revert.
  //
  // There is NO server-side auto-correction of the sibling flags, despite what
  // this comment used to claim. What exists is a read-time override —
  // `_satisfies_room_flags` treats `allow_project_owners_only` as exclusive
  // wherever access is evaluated — plus the disabled toggles below, plus one
  // outright refusal: R13.04's `allow_member_groups` / `allow_project_members`
  // pair is a 422, which `setFlag` avoids by moving both together.
  // Do not reintroduce local coupling between these.
  const flags = computed(() => ({
    allow_org_members: room.value?.allow_org_members ?? false,
    allow_project_members: room.value?.allow_project_members ?? true,
    allow_project_owners_only: room.value?.allow_project_owners_only ?? false,
    allow_guest_links: room.value?.allow_guest_links ?? false,
    allow_member_groups: room.value?.allow_member_groups ?? false,
    // R28.09 — creator-only; the server rejects a non-creator patch of this
    // field, so the UI only exposes the toggle to the creator.
    disclose_observers: room.value?.disclose_observers ?? true,
    // [R32.05] — the same terms. Defaults true while the room read is in flight,
    // matching the column: the direction that over-discloses is the safe one.
    disclose_drafts: room.value?.disclose_drafts ?? true,
  }))

  const loading = ref(true)
  const loadError = ref(false)
  const saving = ref(false)
  const saveError = ref<string | null>(null)

  /** Adopt a server object wholesale, name draft included. */
  function applyRoom(found: Chatroom): void {
    room.value = found
    name.value = found.name
    syncedName = found.name
  }

  /** Adopt a server object the user did not ask for by name: the background
   *  revalidation, and the response to a flag patch (which carries whatever
   *  name the server already had, because the patch deliberately sent none).
   *  Two things it must not trample:
   *
   *  - An in-progress rename. The draft the user is holding is theirs to
   *    submit or discard — overwriting it here would be the rename bleed in
   *    reverse, deleting instead of committing.
   *  - A newer room already in hand. A revalidation issued before a save can
   *    land after it; `version` is monotonic per room, so an older response
   *    is dropped rather than winding the form back to a pre-save state whose
   *    next save would 409. */
  function applyKeepingDraft(found: Chatroom): void {
    if (room.value && found.version < room.value.version) return
    const typing = name.value !== syncedName
    room.value = found
    // `syncedName` only moves with the field it describes. Advancing it while
    // keeping the draft would let a later revalidation conclude the user had
    // stopped typing, purely because their draft happened to match whatever
    // name the server moved to.
    if (!typing) {
      name.value = found.name
      syncedName = found.name
    }
  }

  /** Record a failed save on both surfaces the UI offers: the inline alert in
   *  the General card and a toast next to wherever the user actually clicked
   *  (docs/UI/07-conversation.md — the toggles are nowhere near the alert).
   *  Returns true when the cause was a version conflict. */
  function reportSaveFailure(e: unknown): boolean {
    const conflict = e instanceof ApiError && e.status === 409
    if (conflict) {
      saveError.value = 'conversation.settings.versionConflict'
      toast.warning(t(saveError.value))
    } else {
      saveError.value = 'conversation.settings.saveFailed'
      toast.error(t(saveError.value))
    }
    return conflict
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
      // F-8: paint instantly, then revalidate. The cache entry the prefix
      // match finds may be the sidebar's recent-chatrooms list, which carries
      // a 60s staleTime (useRecentChatrooms) — a form painted from it and
      // never refreshed re-submits stale values behind the fresh version it
      // picks up from a 409, silently reverting another user's save.
      applyRoom(cached)
      loading.value = false
      void revalidate()
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

  async function revalidate(): Promise<void> {
    try {
      applyKeepingDraft(await getChatroom(chatroomId))
    } catch {
      /* the cached paint stands; a save surfaces any real problem */
    }
  }

  /** F-8: a conflict means someone else's values are the current ones, so the
   *  whole form adopts them — `name` included. Refreshing only `room.version`
   *  inverted the control: the mechanism that exists to prevent a stale
   *  overwrite instead authorised one on the retry
   *  (docs/UI/12-shared-patterns.md §4.3). */
  async function resyncAfterConflict(): Promise<void> {
    try {
      applyRoom(await getChatroom(chatroomId))
    } catch {
      /* keep the form as-is; the inline error already explains the retry */
    }
  }

  /** Save the name form, and only the name form. Each flag owns its own patch
   *  now, so a toggle no longer commits a half-typed rename the user never
   *  submitted and this no longer re-sends flags it did not change — which
   *  also makes the `chatroom.updated` audit's `changed` list truthful. */
  async function onSave(): Promise<void> {
    if (!room.value || saving.value) return
    saving.value = true
    saveError.value = null
    try {
      applyRoom(await patchChatroom(chatroomId, room.value.version, { name: name.value }))
      await qc.invalidateQueries({ queryKey: ['conversation', 'chatrooms'] })
      toast.success(t('conversation.settings.saved'))
    } catch (e) {
      if (reportSaveFailure(e)) await resyncAfterConflict()
    } finally {
      saving.value = false
    }
  }

  /** Patch one access field, ending on a server object however it goes.
   *
   *  Takes a patch object rather than a key/value pair because R13.04's one
   *  exclusive pair has to move together: see `setFlag`. */
  async function patchFlag(
    patch: Partial<Record<AccessFlag | 'disclose_observers' | 'disclose_drafts', boolean>>,
  ): Promise<void> {
    if (!room.value || saving.value) return
    saving.value = true
    saveError.value = null
    try {
      applyKeepingDraft(await patchChatroom(chatroomId, room.value.version, patch))
      await qc.invalidateQueries({ queryKey: ['conversation', 'chatrooms'] })
      toast.success(t('conversation.settings.saved'))
    } catch (e) {
      if (reportSaveFailure(e)) {
        await resyncAfterConflict()
      } else {
        // Not a conflict, so the name draft is still the user's — refresh the
        // flags around it rather than adopting the whole room.
        await revalidate()
      }
    } finally {
      saving.value = false
    }
  }

  /** Immediate-save access toggle (docs/UI/07-conversation.md §4.2).
   *
   *  R13.04 makes `allow_member_groups` and `allow_project_members` mutually
   *  exclusive, and the server refuses the pair with a 422. Turning either on
   *  therefore turns the other off in the SAME patch: the user's intent is
   *  unambiguous ("scope this room to groups" / "open it to the project"), and
   *  making them clear the old tier first would surface a validation error for a
   *  state they never asked to be in. Sending both in one request also means the
   *  room is never momentarily open to nobody.
   *
   *  Turning the group tier *off* needs the same care, and for the same reason:
   *  switching it on already cleared `allow_project_members`, so sending
   *  `{allow_member_groups: false}` alone lands the room with no member tier at
   *  all and silently locks every non-moderator out. It is restored only when
   *  nothing else would still admit members — a room that is also org-wide or
   *  owners-only is narrower on purpose, and widening it to the whole project
   *  would be its own unasked-for change. */
  async function setFlag(key: AccessFlag, value: boolean): Promise<void> {
    if (value && key === 'allow_member_groups') {
      // AC-18. The exclusivity above is not a detail of the request, it is a
      // change to who can enter the room: every project member who is in no
      // bound group loses access the moment this lands. A teacher enabling
      // group submission is thinking about submitting, not about the door, and
      // R13.04 is deliberate — so nothing but saying it here can prevent the
      // confused first setup this is the single most likely source of (OQ-2).
      // Asked before the patch rather than explained after it, because after it
      // the students are already locked out.
      if (flags.value.allow_project_members) {
        const ok = await confirm({
          title: t('conversation.settings.memberGroupsExclusiveTitle'),
          message: t('conversation.settings.memberGroupsExclusiveWarning'),
          confirmLabel: t('conversation.settings.memberGroupsExclusiveConfirm'),
          cancelLabel: t('app.cancel'),
          variant: 'warning',
        })
        if (!ok) return
      }
      await patchFlag({ allow_member_groups: true, allow_project_members: false })
      return
    }
    if (value && key === 'allow_project_members') {
      await patchFlag({ allow_project_members: true, allow_member_groups: false })
      return
    }
    if (!value && key === 'allow_member_groups' && !hasOtherMemberTier()) {
      await patchFlag({ allow_member_groups: false, allow_project_members: true })
      return
    }
    await patchFlag({ [key]: value })
  }

  /** Would anything still admit *members* if the group tier were switched off?
   *
   *  `allow_guest_links` deliberately does not count: a guest link admits people
   *  who hold the link, not the project's members, so a room left on guests alone
   *  is still closed to everyone it was built for. */
  function hasOtherMemberTier(): boolean {
    return flags.value.allow_org_members || flags.value.allow_project_owners_only
  }

  /** Creator-only patch of just `disclose_observers` (R28.09). Kept as its own
   *  entry point, not folded into `setFlag`, so the field is never sent by a
   *  non-creator: a generic save carrying it would 403 a non-creator moderator
   *  editing the access flags. */
  async function saveDisclosure(value: boolean): Promise<void> {
    await patchFlag({ disclose_observers: value })
  }

  /** Creator-only patch of just `disclose_drafts` ([R32.05]).
   *
   *  Its own entry point for the same reason its observer sibling is: a generic
   *  save carrying it would 403 a non-creator moderator editing the access flags.
   *  Sent alone rather than paired with the observer flag, so turning one off
   *  never silently moves the other.
   */
  async function saveDraftDisclosure(value: boolean): Promise<void> {
    await patchFlag({ disclose_drafts: value })
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
    setFlag,
    saveDisclosure,
    saveDraftDisclosure,
    onDelete,
  }
}
