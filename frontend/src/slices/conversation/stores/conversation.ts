// Pinia store for presence + ephemeral chat state (R24.22).
//
// Durable state (message list, chatroom list) lives in TanStack Query so the
// cache primitives it already owns don't need re-implementing here. This
// store only holds:
//   - presence sets keyed by chatroom_id
//   - the active chatroom id (navigation convenience)
//   - per-room "agent is thinking" flag surfaced by agent.thinking/.finished
//   - per-room streaming draft accumulated from agent.token deltas
//   - per-room transient agent error kind (agent.finished{error} / timeout)

import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useConversationStore = defineStore('conversation', () => {
  const activeChatroomId = ref<string | null>(null)
  const presence = ref<Record<string, Set<string>>>({})
  const agentThinking = ref<Record<string, boolean>>({})
  // Transient per-token stream of the in-progress agent reply. Cleared when
  // the persisted message arrives (message.created) or the turn errors out.
  const agentStream = ref<Record<string, string>>({})
  // Last agent failure kind ('timeout' for the client-side watchdog, or the
  // backend's error kind from agent.finished). The view consumes + clears it.
  const agentError = ref<Record<string, string | null>>({})
  const typingUsers = ref<Record<string, Set<string>>>({})

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

  function setAgentThinking(roomId: string, value: boolean): void {
    agentThinking.value = { ...agentThinking.value, [roomId]: value }
  }

  function appendAgentToken(roomId: string, text: string): void {
    agentStream.value = {
      ...agentStream.value,
      [roomId]: (agentStream.value[roomId] ?? '') + text,
    }
  }

  function clearAgentStream(roomId: string): void {
    const { [roomId]: _s, ...rest } = agentStream.value
    agentStream.value = rest
  }

  function setAgentError(roomId: string, kind: string | null): void {
    agentError.value = { ...agentError.value, [roomId]: kind }
  }

  function setActive(id: string | null): void {
    activeChatroomId.value = id
  }

  function resetRoom(roomId: string): void {
    const { [roomId]: _p, ...rest } = presence.value
    presence.value = rest
    const { [roomId]: _a, ...restAgent } = agentThinking.value
    agentThinking.value = restAgent
    const { [roomId]: _s, ...restStream } = agentStream.value
    agentStream.value = restStream
    const { [roomId]: _e, ...restError } = agentError.value
    agentError.value = restError
    const { [roomId]: _t, ...restTyping } = typingUsers.value
    typingUsers.value = restTyping
  }

  function clearAll(): void {
    presence.value = {}
    agentThinking.value = {}
    agentStream.value = {}
    agentError.value = {}
    typingUsers.value = {}
    activeChatroomId.value = null
  }

  return {
    activeChatroomId,
    presence,
    agentThinking,
    agentStream,
    agentError,
    typingUsers,
    addTyping,
    removeTyping,
    joinPresence,
    leavePresence,
    setAgentThinking,
    appendAgentToken,
    clearAgentStream,
    setAgentError,
    setActive,
    resetRoom,
    clearAll,
  }
})
