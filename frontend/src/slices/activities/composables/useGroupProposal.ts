// Group-submission state for one round ([R30.41], [R30.42]). Owns the four
// proposal calls and the decision of when a room broadcast is worth a refetch;
// the panel renders what this returns and holds none of it itself (§9 of the
// dossier — ActivityPanel is already the slice's largest component).
//
// The slice imports no conversation state: the room id, the activation and the
// caller arrive as getters from the panel's props, exactly as the rest of the
// slice takes them.

import { computed, ref, toValue, watch, type ComputedRef, type MaybeRefOrGetter, type Ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { ApiError } from '@shared/errors'
import {
  createGroupProposal,
  listGroupProposals,
  voteOnGroupProposal,
  withdrawGroupProposal,
} from '../api'
import { useActivitiesStore } from '../stores/activities'
import { isProposalOpen, type ActivityGroupProposal, type ActivityMemberGroupRef } from '../types'

export interface UseGroupProposalOptions {
  chatroomId: MaybeRefOrGetter<string>
  /** Null while no round is running; the composable then holds no state. */
  activationId: MaybeRefOrGetter<string | null>
  activityTypeId: MaybeRefOrGetter<string | null>
  /** The viewer, for "have I voted yet". Null for a session that has not
   *  resolved its own identity, which disables the vote controls. */
  viewerUserId: MaybeRefOrGetter<string | null>
  /** The room creator reads every bound group's vote, so any group's broadcast
   *  is theirs to follow up on. A participant follows up only on their own. */
  isCreator?: MaybeRefOrGetter<boolean>
}

export interface UseGroupProposal {
  proposals: ComputedRef<ActivityGroupProposal[]>
  eligibleGroups: ComputedRef<ActivityMemberGroupRef[]>
  /** The one open proposal for a group this caller belongs to, or null. At most
   *  one exists per (activation, group) and the panel only ever offers the
   *  caller's own groups, so a single card is the whole participant surface. */
  openProposal: ComputedRef<ActivityGroupProposal | null>
  /** The proposal to SHOW: the open one, or the one that settled under this
   *  caller in this session.
   *
   *  Distinct from `openProposal` because a card that disappears the instant the
   *  vote lands is the worst moment to remove it — the student who cast the
   *  deciding vote gets no word that their group's answer went in, and the blank
   *  propose form returning in its place invites a second proposal that the
   *  partial unique does not stop (it bars only concurrent OPEN ones), producing
   *  a duplicate attempt for the group. The listing returns open proposals only,
   *  so this is the sole way a terminal state is ever seen. */
  visibleProposal: ComputedRef<ActivityGroupProposal | null>
  /** Whether the panel should offer group mode at all: the server said this
   *  caller belongs to at least one group bound to this room. */
  canPropose: ComputedRef<boolean>
  /** Whether the server has answered for THIS round yet.
   *
   *  `canPropose` alone cannot carry this: it is false both for a caller in no
   *  group and for one whose read has not returned, and a panel that treats
   *  those the same shows the individual worksheet to a group participant until
   *  the read lands — long enough to type an answer into a surface that is
   *  about to be replaced. */
  roundResolved: ComputedRef<boolean>
  /** This caller's own decision on the open proposal: true approved, false
   *  rejected, null undecided. Also null for a caller the server did not send
   *  the per-person record to, which is the same thing as far as the controls
   *  go — the server refuses a second vote either way. */
  myVote: ComputedRef<boolean | null>
  /** Only the proposer may withdraw, so only they see the control. */
  isProposer: ComputedRef<boolean>
  loading: Ref<boolean>
  pending: Ref<boolean>
  errorMessage: Ref<string | null>
  groupName(groupId: string): string | null
  /** Put the settled outcome away and go back to the propose form. Only for a
   *  proposal that did NOT accept: a rejected, withdrawn or expired one leaves
   *  the group free to try again, an accepted one is the round's answer. */
  dismissSettled(): void
  refresh(): Promise<void>
  propose(memberGroupId: string, payload: Record<string, unknown>): Promise<void>
  vote(proposalId: string, approve: boolean): Promise<void>
  withdraw(proposalId: string): Promise<void>
}

export function useGroupProposal(options: UseGroupProposalOptions): UseGroupProposal {
  const store = useActivitiesStore()
  const { t } = useI18n()

  const loading = ref(false)
  const pending = ref(false)
  const errorMessage = ref<string | null>(null)

  // Guards a slower read for a round that has since ended from clobbering a
  // fresher one, the way the panel's own type fetch is guarded.
  let fetchGeneration = 0

  const room = computed(() => store.getProposalRoom(toValue(options.chatroomId)))
  const activeRoom = computed(() => {
    const activationId = toValue(options.activationId)
    const state = room.value
    // State from a previous round is not this round's absence of proposals: it
    // would render a settled card under a freshly started activity.
    return state && activationId && state.activationId === activationId ? state : undefined
  })

  const proposals = computed<ActivityGroupProposal[]>(() =>
    Object.values(activeRoom.value?.proposals ?? {}),
  )
  const eligibleGroups = computed<ActivityMemberGroupRef[]>(
    () => activeRoom.value?.eligibleGroups ?? [],
  )
  const canPropose = computed(() => eligibleGroups.value.length > 0)
  // Keyed on the store entry for THIS activation, not on `loading`: the watcher
  // that starts the read is async, so there is a tick where the round has
  // changed and no request is in flight yet. `loading` reads false in it.
  const roundResolved = computed(() => activeRoom.value !== undefined)

  const openProposal = computed<ActivityGroupProposal | null>(() => {
    const mine = new Set(eligibleGroups.value.map((g) => g.id))
    return (
      proposals.value.find((p) => isProposalOpen(p.status) && mine.has(p.member_group_id)) ?? null
    )
  })

  // The id of a proposal that settled while this caller was watching. Held
  // rather than derived, because `setRound` drops resolved proposals -- the
  // listing returns only open ones -- so a refetch triggered by anything else in
  // the round would otherwise take the outcome off the screen mid-read.
  const settledId = ref<string | null>(null)
  watch(openProposal, (next, previous) => {
    if (previous && !next) settledId.value = previous.id
    else if (next) settledId.value = null
  })
  // A new round is a new question; last round's outcome is not part of it.
  watch(
    () => toValue(options.activationId),
    () => {
      settledId.value = null
    },
  )

  const visibleProposal = computed<ActivityGroupProposal | null>(
    () =>
      openProposal.value ??
      (settledId.value ? (proposals.value.find((p) => p.id === settledId.value) ?? null) : null),
  )

  const myVote = computed<boolean | null>(() => {
    const viewer = toValue(options.viewerUserId)
    if (!viewer) return null
    return visibleProposal.value?.votes.find((v) => v.user_id === viewer)?.approve ?? null
  })

  const isProposer = computed(() => {
    const viewer = toValue(options.viewerUserId)
    return !!viewer && visibleProposal.value?.proposer_user_id === viewer
  })

  function groupName(groupId: string): string | null {
    return eligibleGroups.value.find((g) => g.id === groupId)?.name ?? null
  }

  function dismissSettled(): void {
    settledId.value = null
  }

  function report(err: unknown, fallback: string): void {
    errorMessage.value = err instanceof ApiError ? err.message : t(fallback)
  }

  /** Re-read the round.
   *
   *  `keepError` exists for the one caller that refetches BECAUSE something was
   *  refused: a 409 on a vote (§7). Clearing the message there would leave the
   *  card silently corrected with no word about why the click did nothing, which
   *  is the state the refetch exists to avoid, not to produce. */
  async function refresh(keepError = false): Promise<void> {
    const chatroomId = toValue(options.chatroomId)
    const activationId = toValue(options.activationId)
    if (!activationId) {
      store.clearProposals(chatroomId)
      return
    }
    const generation = ++fetchGeneration
    loading.value = true
    try {
      const round = await listGroupProposals(chatroomId, activationId)
      if (generation !== fetchGeneration) return
      store.setRound(chatroomId, {
        activationId,
        proposals: round.items,
        eligibleGroups: round.eligible_groups ?? [],
      })
      if (!keepError) errorMessage.value = null
    } catch (err) {
      if (generation !== fetchGeneration) return
      // A room member in no group gets an empty list, not a 403, so a failure
      // here is a real one and must not be swallowed into "no group mode".
      report(err, 'activities.group.loadFailed')
    } finally {
      if (generation === fetchGeneration) loading.value = false
    }
  }

  /** Run one mutation, adopting whatever proposal the server returns.
   *
   *  A 409 means the proposal settled under this caller (§7): the response body
   *  is a refusal rather than a tally, so the card is refetched instead of left
   *  showing the count the click was made against. */
  async function mutate(
    call: () => Promise<ActivityGroupProposal>,
    fallback: string,
  ): Promise<void> {
    if (pending.value) return
    pending.value = true
    errorMessage.value = null
    try {
      store.upsertProposal(toValue(options.chatroomId), await call())
    } catch (err) {
      report(err, fallback)
      if (err instanceof ApiError && (err.status === 409 || err.status === 404)) {
        await refresh(true)
      }
    } finally {
      pending.value = false
    }
  }

  async function propose(
    memberGroupId: string,
    payload: Record<string, unknown>,
  ): Promise<void> {
    const activityTypeId = toValue(options.activityTypeId)
    if (!activityTypeId) return
    await mutate(
      () =>
        createGroupProposal(toValue(options.chatroomId), {
          activity_type_id: activityTypeId,
          member_group_id: memberGroupId,
          payload,
        }),
      'activities.group.proposeFailed',
    )
  }

  async function vote(proposalId: string, approve: boolean): Promise<void> {
    await mutate(
      () => voteOnGroupProposal(toValue(options.chatroomId), proposalId, approve),
      'activities.group.voteFailed',
    )
  }

  async function withdraw(proposalId: string): Promise<void> {
    await mutate(
      () => withdrawGroupProposal(toValue(options.chatroomId), proposalId),
      'activities.group.withdrawFailed',
    )
  }

  // Seed on every round change, and drop the previous round's state on the way
  // out so a card cannot survive into an activity it does not belong to.
  watch(
    () => [toValue(options.chatroomId), toValue(options.activationId)] as const,
    () => {
      void refresh()
    },
    { immediate: true },
  )

  // Follow up on a broadcast the store could not apply. The store deliberately
  // refuses to insert a proposal it was only told about over the room channel
  // ([R30.42]); this is where that becomes a re-read, and only for a group this
  // caller has business seeing — otherwise every group's opening would make
  // every student in the room refetch.
  watch(
    () => activeRoom.value?.version ?? 0,
    () => {
      const state = activeRoom.value
      if (!state?.unseenGroupIds.length) return
      const mine = new Set(state.eligibleGroups.map((g) => g.id))
      const relevant =
        toValue(options.isCreator) === true || state.unseenGroupIds.some((id) => mine.has(id))
      if (relevant) void refresh()
    },
  )

  return {
    proposals,
    eligibleGroups,
    openProposal,
    visibleProposal,
    canPropose,
    roundResolved,
    myVote,
    isProposer,
    loading,
    pending,
    errorMessage,
    groupName,
    dismissSettled,
    refresh,
    propose,
    vote,
    withdraw,
  }
}
