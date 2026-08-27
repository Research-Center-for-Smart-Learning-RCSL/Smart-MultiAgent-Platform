// Maps a backend `agent.finished{error}` kind (plus the client-side watchdog
// 'timeout') to the i18n key shown for it. Shared by the one-shot toast
// (ChatroomView), the sidebar badge tooltip (ChatroomAgentStatusItem) and the
// observer rail (ObserverPanel) so the surfaces can't drift. Resolve through
// `agentErrorMessageKey` rather than indexing this map directly — some kinds
// carry a `:reason` suffix that a bare lookup would miss.
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
  // The provider call itself failed. Each of these used to land on the generic
  // fallback, which told an operator nothing and hid the single most common
  // real cause of a failed turn (a rejected key or an unknown model id).
  provider_stream_failed: 'conversation.chatroom.agentProviderStreamFailed',
  database_error: 'conversation.chatroom.agentDatabaseError',
  'provider_exhausted:quota': 'conversation.chatroom.agentProviderQuota',
  'provider_exhausted:errors': 'conversation.chatroom.agentProviderErrors',
  'provider_exhausted:request_rejected': 'conversation.chatroom.agentProviderRejected',
}

// Kinds the backend emits as `family:reason`. When the reason is one the map
// above does not name (a new router reason shipping ahead of the frontend), the
// family's own copy is still far more useful than the generic fallback.
export const AGENT_ERROR_FAMILY_KEYS: Record<string, string> = {
  provider_exhausted: 'conversation.chatroom.agentProviderExhausted',
}

export const AGENT_ERROR_FALLBACK_KEY = 'conversation.chatroom.agentFailed'

/** Resolve a backend error kind to its i18n key: exact match, then the
 *  `family:reason` family, then the generic fallback. */
export function agentErrorMessageKey(kind: string | null | undefined): string {
  // `Object.hasOwn`, not a bare index: an unmapped backend exception reaches us
  // as its Python class name (turn_engine `_err_kind`), so a class named
  // `constructor` or `toString` would otherwise resolve to an inherited function
  // and be handed to `t()` as if it were an i18n key.
  if (!kind) return AGENT_ERROR_FALLBACK_KEY
  const exact = own(AGENT_ERROR_MESSAGE_KEYS, kind)
  if (exact) return exact
  const separator = kind.indexOf(':')
  if (separator > 0) {
    const family = own(AGENT_ERROR_FAMILY_KEYS, kind.slice(0, separator))
    if (family) return family
  }
  return AGENT_ERROR_FALLBACK_KEY
}

function own(map: Record<string, string>, key: string): string | undefined {
  return Object.hasOwn(map, key) ? map[key] : undefined
}
