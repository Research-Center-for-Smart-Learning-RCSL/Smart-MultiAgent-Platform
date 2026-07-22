import { describe, it, expect } from 'vitest'

import en from '../locales/en.json'
import zhTW from '../locales/zh-TW.json'
import { AGENT_ERROR_MESSAGE_KEYS, AGENT_ERROR_FALLBACK_KEY } from '../constants/agentErrors'

// Walks 'conversation.chatroom.agentFailed' down a locale object. The files are
// rooted at their own 'conversation' key, so the map's fully-qualified keys are
// walked whole.
function resolve(locale: Record<string, unknown>, key: string): unknown {
  return key.split('.').reduce<unknown>((node, segment) => {
    if (node && typeof node === 'object' && segment in node) {
      return (node as Record<string, unknown>)[segment]
    }
    return undefined
  }, locale)
}

const LOCALES: Array<[string, Record<string, unknown>]> = [
  ['en', en as Record<string, unknown>],
  ['zh-TW', zhTW as Record<string, unknown>],
]

describe('AGENT_ERROR_MESSAGE_KEYS', () => {
  it('maps knowledge_starved to its own message, not the generic unavailable one', () => {
    // AC-11: the cause is the agent's own token cap, which the reader can raise.
    // Falling back to 'agentFailed' would tell them to "try again" — advice that
    // cannot work, because the next turn starves identically.
    expect(AGENT_ERROR_MESSAGE_KEYS.knowledge_starved).toBe(
      'conversation.chatroom.agentKnowledgeStarved',
    )
    expect(AGENT_ERROR_MESSAGE_KEYS.knowledge_starved).not.toBe(AGENT_ERROR_FALLBACK_KEY)
    expect(AGENT_ERROR_MESSAGE_KEYS.knowledge_starved).not.toBe(
      AGENT_ERROR_MESSAGE_KEYS.key_group_scope,
    )
  })

  it('maps an unserviceable model hint to actionable configuration guidance', () => {
    expect(AGENT_ERROR_MESSAGE_KEYS.model_hint_unserviceable).toBe(
      'conversation.chatroom.agentModelHintUnserviceable',
    )
    expect(AGENT_ERROR_MESSAGE_KEYS.model_hint_unserviceable).not.toBe(AGENT_ERROR_FALLBACK_KEY)
  })

  it.each(LOCALES)('resolves every mapped key plus the fallback in %s', (_name, locale) => {
    // No CI gate catches a key present in en but missing in zh-TW; this is that
    // gate for the agent-error surface.
    for (const key of [...Object.values(AGENT_ERROR_MESSAGE_KEYS), AGENT_ERROR_FALLBACK_KEY]) {
      const message = resolve(locale, key)
      expect(message, `missing i18n key: ${key}`).toBeTypeOf('string')
      expect(message as string).not.toHaveLength(0)
    }
  })

  it.each(LOCALES)('keeps agent-error copy free of vue-i18n linked-message syntax in %s', (_name, locale) => {
    // A literal '@' in a message body is parsed by vue-i18n as a linked message
    // and throws at runtime in prod, where dev and test only warn.
    for (const key of [...Object.values(AGENT_ERROR_MESSAGE_KEYS), AGENT_ERROR_FALLBACK_KEY]) {
      expect(resolve(locale, key) as string).not.toContain('@')
    }
  })
})
