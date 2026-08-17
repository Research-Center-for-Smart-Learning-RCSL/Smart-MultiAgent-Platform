// The facilitator's view of one round: how many participants have declared
// themselves finished, and how many are still working ([R30.22]).
//
// Lives outside the panel because it owns three things a component should not
// have to hold at once -- an HTTP seed, a WebSocket subscription with its own
// teardown, and a poll fallback. The panel reads one ref.
//
// WHY THE EVENT IS NOT ON THE ROOM CHANNEL
// The counts are the facilitator's, not the class's: in a two-person group
// "1 finished" names the other participant. So the backend addresses
// `activity.session.completion` at the user who started the round. A viewer who
// passes the room-creator gate without being that user -- a platform admin, a
// moderator on a legacy NULL-creator room -- therefore receives nothing and has
// to poll, which is exactly the split `useObservations` makes for observations
// ([R28.13]) and for the same reason.

import { onScopeDispose, ref, toValue, watch, type MaybeRefOrGetter, type Ref } from 'vue'
import { wsManager, type ChannelEvent } from '@shared/transport'
import { getActivationProgress } from '../api'
import type { ActivityActivationProgress } from '../types'

/** How often a non-starter facilitator re-reads the counts. Long on purpose:
 *  this is a classroom pace, and the read is room-creator gated so it is never
 *  a participant's request. */
const POLL_MS = 30_000

export interface UseActivationProgressOptions {
  chatroomId: MaybeRefOrGetter<string>
  /** Whether the viewer may read the counts at all; the endpoint is
   *  room-creator gated, so a participant must not even ask. */
  isCreator: MaybeRefOrGetter<boolean>
  activationId: MaybeRefOrGetter<string | null>
  /** The user the completion event is addressed to. */
  startedByUserId: MaybeRefOrGetter<string | null>
  viewerUserId: MaybeRefOrGetter<string | null>
}

export interface UseActivationProgress {
  progress: Ref<ActivityActivationProgress | null>
  refresh: () => Promise<void>
}

export function useActivationProgress(
  options: UseActivationProgressOptions,
): UseActivationProgress {
  const progress = ref<ActivityActivationProgress | null>(null)

  /** Guarded on the activation the read was issued for rather than a counter:
   *  the round is the identity, so a slower read for a round that has since
   *  ended cannot clobber a fresher one. */
  async function refresh(): Promise<void> {
    const chatroomId = toValue(options.chatroomId)
    const activationId = toValue(options.activationId)
    if (!activationId || !toValue(options.isCreator)) return
    try {
      const next = await getActivationProgress(chatroomId, activationId)
      if (toValue(options.activationId) !== activationId) return
      progress.value = next
    } catch {
      // Room-creator gated: a 403 is the expected answer for a viewer who is not
      // the creator, and a transient failure must not blank a count that was
      // correct a moment ago. Either way the poll or the next event recovers.
    }
  }

  const unsubs: Array<() => void> = []
  let pollTimer: ReturnType<typeof setInterval> | null = null

  function teardown(): void {
    for (const u of unsubs.splice(0)) u()
    if (pollTimer !== null) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  watch(
    () => [
      toValue(options.viewerUserId),
      toValue(options.isCreator),
      toValue(options.activationId),
      toValue(options.startedByUserId),
    ] as const,
    ([viewerUserId, isCreator, activationId, startedByUserId]) => {
      teardown()
      progress.value = null
      if (!viewerUserId || !isCreator || !activationId) return
      void refresh()
      if (startedByUserId === viewerUserId) {
        const channel = wsManager.channel(`/user/${viewerUserId}`)
        unsubs.push(
          channel.subscribe('activity.session.completion', (ev: ChannelEvent) => {
            if (
              ev.chatroom_id !== toValue(options.chatroomId) ||
              ev.activation_id !== activationId
            ) {
              return
            }
            progress.value = {
              completed: Number(ev.completed ?? 0),
              in_progress: Number(ev.in_progress ?? 0),
            }
          }),
        )
        // Idempotent — ban-kick / notifications likely connected it already.
        channel.connect()
      } else {
        pollTimer = setInterval(() => void refresh(), POLL_MS)
      }
    },
    { immediate: true },
  )

  onScopeDispose(teardown)

  return { progress, refresh }
}
