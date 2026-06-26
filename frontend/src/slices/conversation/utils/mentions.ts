// @mention resolution for the chat composer.
//
// A user can summon a specific agent by typing `@AgentName` in a message. The
// backend wakes only agents actually bound to the room, but it trusts the
// resolved id list the client sends — so resolution happens here, against the
// room's known agents, rather than by re-parsing names on the server.

export interface MentionableAgent {
  id: string
  name: string
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

/**
 * Resolve `@AgentName` mentions in `text` to the matching agent ids.
 *
 * A name matches when it appears as `@<name>` at a word start (preceded by
 * whitespace or the string start, so `foo@bar` email-style text never matches)
 * and is not the prefix of a longer token. Longer names are matched first so
 * `@Data Analyst` wins over a hypothetical `@Data`. Returns deduped ids in the
 * order their agents were given.
 */
export function resolveMentions(text: string, agents: MentionableAgent[]): string[] {
  if (!text.includes('@') || agents.length === 0) return []
  // Match longest names first and record each consumed `@name` span, so a longer
  // mention (`@Data Analyst`) blocks a shorter name that is only its prefix
  // (`@Data`) from also matching the same text.
  const ordered = [...agents].sort((a, b) => b.name.length - a.name.length)
  const consumed: Array<[number, number]> = []
  const matched = new Set<string>()
  for (const agent of ordered) {
    if (!agent.name) continue
    const re = new RegExp(`(^|\\s)@${escapeRegExp(agent.name)}(?![\\w])`, 'gi')
    let m: RegExpExecArray | null
    while ((m = re.exec(text)) !== null) {
      const atStart = m.index + m[1]!.length
      const end = atStart + 1 + agent.name.length
      if (!consumed.some(([s, e]) => atStart >= s && atStart < e)) {
        consumed.push([atStart, end])
        matched.add(agent.id)
      }
    }
  }
  // Preserve the caller's agent order in the output.
  return agents.filter((a) => matched.has(a.id)).map((a) => a.id)
}

/**
 * Inspect the text up to the caret for an in-progress `@token` (no whitespace
 * after the `@`). Returns the token's start offset and the partial query so the
 * composer can show an autocomplete list, or null when the caret is not inside
 * a mention.
 */
export function activeMention(text: string, caret: number): { start: number; query: string } | null {
  const before = text.slice(0, caret)
  const match = /(?:^|\s)@([^\s@]*)$/.exec(before)
  if (!match) return null
  return { start: caret - match[1]!.length - 1, query: match[1]! }
}
