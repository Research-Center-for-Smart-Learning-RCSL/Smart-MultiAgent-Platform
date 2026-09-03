// Pinia store for presence + ephemeral chat state (R24.22).
//
// Durable state (message list, chatroom list) lives in TanStack Query so the
// cache primitives it already owns don't need re-implementing here. This
// store only holds:
//   - presence sets keyed by chatroom_id
//   - the active chatroom id (navigation convenience)
//   - per-room per-agent "agent is thinking" flag surfaced by agent.thinking/.finished
//   - per-room per-agent streaming draft accumulated from agent.token deltas
//   - per-room transient agent error kind (agent.finished{error} / timeout)

import { defineStore } from 'pinia'
import { ref } from 'vue'
import { registerCleanup } from '@shared/stores/useAppCleanup'

export const useConversationStore = defineStore('conversation', () => {
  const activeChatroomId = ref<string | null>(null)
  const presence = ref<Record<string, Set<string>>>({})
  // Per-room set of agent IDs that are currently running a turn.
  const agentThinking = ref<Record<string, Set<string>>>({})
  // Per-room per-agent streaming draft accumulated from agent.token deltas.
  const agentStreams = ref<Record<string, Record<string, string>>>({})
  // Last agent failure kind ('timeout' for the client-side watchdog, or the
  // backend's error kind from agent.finished). The view consumes + clears it.
  const agentError = ref<Record<string, string | null>>({})
  // Per-room per-agent error kind that persists until the agent next acts.
  // Drives the sidebar 'error' badge + tooltip — distinct from the transient
  // room-level `agentError` above, which fires the one-shot toast.
  const agentErrors = ref<Record<string, Record<string, string>>>({})
  const typingUsers = ref<Record<string, Set<string>>>({})
  // Per-room set of observer agent IDs currently running an analysis turn
  // (observation.started/.created/.failed on the /user channel — R28.13).
  // Creator-only by construction: the events never reach anyone else.
  const observerAnalyzing = ref<Record<string, Set<string>>>({})
  // Per-room per-observer error kind, persisted until the observer next acts.
  const observerErrors = ref<Record<string, Record<string, string>>>({})
  // Per-room per-observer benign-skip kind (observation.skipped — O-4/R28.13).
  // Distinct from observerErrors so a "nothing to add" never renders as a failure.
  const observerSkips = ref<Record<string, Record<string, string>>>({})

  function addTyping(roomId: string, userId: string): void {
    const set = typingUsers.value[roomId] ?? new Set<string>()
    set.add(userId)
    typingUsers.value = { ...typingUsers.value, [roomId]: set }
  }

  function removeTyping(roomId: string, userId: string): void {
    const set = typingUsers.value[roomId]
    if (!set) return
    set.delete(userId)
    typingUsers.value = { ...typingUsers.value, [roomId]: set }
  }

  function joinPresence(roomId: string, userId: string): void {
    const set = presence.value[roomId] ?? new Set<string>()
    set.add(userId)
    presence.value = { ...presence.value, [roomId]: set }
  }

  function leavePresence(roomId: string, userId: string): void {
    const set = presence.value[roomId]
    if (!set) return
    set.delete(userId)
    presence.value = { ...presence.value, [roomId]: set }
  }

  function setPresence(roomId: string, userIds: string[]): void {
    presence.value = { ...presence.value, [roomId]: new Set(userIds) }
  }

  function clearTyping(roomId: string): void {
    const current = typingUsers.value[roomId]
    if (!current || current.size === 0) return
    typingUsers.value = { ...typingUsers.value, [roomId]: new Set<string>() }
  }

  function setAgentThinking(roomId: string, agentId: string, value: boolean): void {
    const set = agentThinking.value[roomId] ?? new Set<string>()
    if (value) {
      set.add(agentId)
    } else {
      set.delete(agentId)
    }
    agentThinking.value = { ...agentThinking.value, [roomId]: set }
  }

  function clearAllAgentThinking(roomId: string): void {
    const { [roomId]: _, ...rest } = agentThinking.value
    agentThinking.value = rest
  }

  function isAnyAgentThinking(roomId: string): boolean {
    const set = agentThinking.value[roomId]
    return !!set && set.size > 0
  }

  function appendAgentToken(roomId: string, agentId: string, text: string): void {
    const roomStreams = agentStreams.value[roomId] ?? {}
    agentStreams.value = {
      ...agentStreams.value,
      [roomId]: { ...roomStreams, [agentId]: (roomStreams[agentId] ?? '') + text },
    }
  }

  function clearAgentStream(roomId: string, agentId?: string): void {
    if (agentId) {
      const roomStreams = agentStreams.value[roomId]
      if (!roomStreams) return
      const { [agentId]: _, ...rest } = roomStreams
      if (Object.keys(rest).length === 0) {
        const { [roomId]: _r, ...roomsRest } = agentStreams.value
        agentStreams.value = roomsRest
      } else {
        agentStreams.value = { ...agentStreams.value, [roomId]: rest }
      }
    } else {
      const { [roomId]: _, ...rest } = agentStreams.value
      agentStreams.value = rest
    }
  }

  function setAgentError(roomId: string, kind: string | null): void {
    agentError.value = { ...agentError.value, [roomId]: kind }
  }

  function setAgentErrorKind(roomId: string, agentId: string, kind: string): void {
    const room = agentErrors.value[roomId] ?? {}
    agentErrors.value = { ...agentErrors.value, [roomId]: { ...room, [agentId]: kind } }
  }

  function clearAgentError(roomId: string, agentId: string): void {
    const room = agentErrors.value[roomId]
    if (!room || !(agentId in room)) return
    const { [agentId]: _, ...rest } = room
    if (Object.keys(rest).length === 0) {
      const { [roomId]: _r, ...roomsRest } = agentErrors.value
      agentErrors.value = roomsRest
    } else {
      agentErrors.value = { ...agentErrors.value, [roomId]: rest }
    }
  }

  function setObserverAnalyzing(roomId: string, agentId: string, value: boolean): void {
    const set = new Set(observerAnalyzing.value[roomId] ?? [])
    if (value) {
      set.add(agentId)
    } else {
      set.delete(agentId)
    }
    observerAnalyzing.value = { ...observerAnalyzing.value, [roomId]: set }
  }

  // F-8's two guards both need to declare every running analysis over at once:
  // a reconnect (the terminal frame is unrecoverable — Redis pub/sub does not
  // replay) and the watchdog (a killed worker will never emit one). Mirrors
  // clearAllAgentThinking on the room path.
  function clearAllObserverAnalyzing(roomId: string): void {
    const { [roomId]: _, ...rest } = observerAnalyzing.value
    observerAnalyzing.value = rest
  }

  function setObserverErrorKind(roomId: string, agentId: string, kind: string): void {
    const room = observerErrors.value[roomId] ?? {}
    observerErrors.value = { ...observerErrors.value, [roomId]: { ...room, [agentId]: kind } }
  }

  function clearObserverError(roomId: string, agentId: string): void {
    const room = observerErrors.value[roomId]
    if (!room || !(agentId in room)) return
    const { [agentId]: _, ...rest } = room
    if (Object.keys(rest).length === 0) {
      const { [roomId]: _r, ...roomsRest } = observerErrors.value
      observerErrors.value = roomsRest
    } else {
      observerErrors.value = { ...observerErrors.value, [roomId]: rest }
    }
  }

  function setObserverSkipKind(roomId: string, agentId: string, kind: string): void {
    const room = observerSkips.value[roomId] ?? {}
    observerSkips.value = { ...observerSkips.value, [roomId]: { ...room, [agentId]: kind } }
  }

  function clearObserverSkip(roomId: string, agentId: string): void {
    const room = observerSkips.value[roomId]
    if (!room || !(agentId in room)) return
    const { [agentId]: _, ...rest } = room
    if (Object.keys(rest).length === 0) {
      const { [roomId]: _r, ...roomsRest } = observerSkips.value
      observerSkips.value = roomsRest
    } else {
      observerSkips.value = { ...observerSkips.value, [roomId]: rest }
    }
  }

  function setActive(id: string | null): void {
    activeChatroomId.value = id
  }

  function resetRoom(roomId: string): void {
    const { [roomId]: _p, ...rest } = presence.value
    presence.value = rest
    const { [roomId]: _a, ...restAgent } = agentThinking.value
    agentThinking.value = restAgent
    const { [roomId]: _s, ...restStream } = agentStreams.value
    agentStreams.value = restStream
    const { [roomId]: _e, ...restError } = agentError.value
    agentError.value = restError
    const { [roomId]: _ae, ...restAgentErrors } = agentErrors.value
    agentErrors.value = restAgentErrors
    const { [roomId]: _t, ...restTyping } = typingUsers.value
    typingUsers.value = restTyping
    const { [roomId]: _oa, ...restObserverAnalyzing } = observerAnalyzing.value
    observerAnalyzing.value = restObserverAnalyzing
    const { [roomId]: _oe, ...restObserverErrors } = observerErrors.value
    observerErrors.value = restObserverErrors
    const { [roomId]: _os, ...restObserverSkips } = observerSkips.value
    observerSkips.value = restObserverSkips
  }

  function clearAll(): void {
    presence.value = {}
    agentThinking.value = {}
    agentStreams.value = {}
    agentError.value = {}
    agentErrors.value = {}
    typingUsers.value = {}
    observerAnalyzing.value = {}
    observerErrors.value = {}
    observerSkips.value = {}
    activeChatroomId.value = null
  }

  // Register with the shared cleanup registry so session.clear() can
  // reset conversation state without importing this store directly (H14).
  registerCleanup(clearAll)

  return {
    activeChatroomId,
    presence,
    agentThinking,
    agentStreams,
    agentError,
    agentErrors,
    typingUsers,
    observerAnalyzing,
    observerErrors,
    observerSkips,
    setObserverAnalyzing,
    clearAllObserverAnalyzing,
    setObserverErrorKind,
    clearObserverError,
    setObserverSkipKind,
    clearObserverSkip,
    addTyping,
    removeTyping,
    joinPresence,
    leavePresence,
    setPresence,
    clearTyping,
    setAgentThinking,
    clearAllAgentThinking,
    isAnyAgentThinking,
    appendAgentToken,
    clearAgentStream,
    setAgentError,
    setAgentErrorKind,
    clearAgentError,
    setActive,
    resetRoom,
    clearAll,
  }
})
