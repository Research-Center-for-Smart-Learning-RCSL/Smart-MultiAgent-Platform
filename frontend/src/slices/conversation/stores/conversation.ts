// Pinia store for presence + ephemeral chat state (R24.22).
//
// Durable state (message list, chatroom list) lives in TanStack Query so the
// cache primitives it already owns don't need re-implementing here. This
// store only holds:
//   - presence sets keyed by chatroom_id
//   - the active chatroom id (navigation convenience)
//   - per-room "agent is thinking" flag surfaced by agent.thinking/.finished

import { defineStore } from 'pinia'
import { ref } from 'vue'

export const useConversationStore = defineStore('conversation', () => {
  const activeChatroomId = ref<string | null>(null)
  const presence = ref<Record<string, Set<string>>>({})
  const agentThinking = ref<Record<string, boolean>>({})

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

  function setActive(id: string | null): void {
    activeChatroomId.value = id
  }

  function resetRoom(roomId: string): void {
    const { [roomId]: _p, ...rest } = presence.value
    presence.value = rest
    const { [roomId]: _a, ...restAgent } = agentThinking.value
    agentThinking.value = restAgent
  }

  return {
    activeChatroomId,
    presence,
    agentThinking,
    joinPresence,
    leavePresence,
    setAgentThinking,
    setActive,
    resetRoom,
  }
})
