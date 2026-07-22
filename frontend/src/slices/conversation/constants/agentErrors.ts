// Maps a backend `agent.finished{error}` kind (plus the client-side watchdog
// 'timeout') to the i18n key shown for it. Shared by the one-shot toast
// (ChatroomView) and the sidebar badge tooltip (ChatroomAgentStatusItem) so the
// two surfaces can't drift. Unknown kinds (e.g. `provider_exhausted:*`) fall
// back to AGENT_ERROR_FALLBACK_KEY at the call site.
export const AGENT_ERROR_MESSAGE_KEYS: Record<string, string> = {
  timeout: 'conversation.chatroom.agentTimeout',
  rate_limited: 'conversation.chatroom.agentRateLimited',
  agent_gone: 'conversation.chatroom.agentUnavailable',
  not_bound: 'conversation.chatroom.agentUnavailable',
  key_group_scope: 'conversation.chatroom.agentUnavailable',
  // Not 'agentUnavailable': the agent is fine and the cause is a setting the
  // reader can change, so the copy has to name it.
  knowledge_starved: 'conversation.chatroom.agentKnowledgeStarved',
  model_hint_unserviceable: 'conversation.chatroom.agentModelHintUnserviceable',
}

export const AGENT_ERROR_FALLBACK_KEY = 'conversation.chatroom.agentFailed'
