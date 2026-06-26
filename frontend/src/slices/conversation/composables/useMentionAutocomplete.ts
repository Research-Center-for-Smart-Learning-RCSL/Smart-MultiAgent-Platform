// @mention autocomplete state machine for the chat composer.
//
// Owns the "is the caret inside an @token, and which agents match" logic plus
// the keyboard navigation and insertion, so the composer component only wires
// events to it and renders the match list. Resolution of a sent message's
// mentions lives in `utils/mentions.ts` and is shared with the send path.

import { computed, nextTick, ref, type Ref } from 'vue'

import { activeMention, type MentionableAgent } from '../utils/mentions'

const MENTION_LIMIT = 6
const MENTION_NAV_KEYS = ['ArrowDown', 'ArrowUp', 'Enter', 'Tab', 'Escape']

export function useMentionAutocomplete(options: {
  textarea: Ref<HTMLTextAreaElement | null>
  agents: () => MentionableAgent[]
  onInsert: (value: string) => void
}) {
  const { textarea, agents, onInsert } = options

  // The partial token after an active `@`, or null when the caret is not inside
  // a mention.
  const query = ref<string | null>(null)
  const activeIndex = ref(0)

  const matches = computed<MentionableAgent[]>(() => {
    if (query.value === null) return []
    const q = query.value.toLowerCase()
    return agents()
      .filter((a) => a.name.toLowerCase().includes(q))
      .slice(0, MENTION_LIMIT)
  })
  const open = computed(() => query.value !== null && matches.value.length > 0)

  function refresh(): void {
    const el = textarea.value
    if (!el) {
      query.value = null
      return
    }
    const info = activeMention(el.value, el.selectionStart ?? 0)
    query.value = info ? info.query : null
    activeIndex.value = 0
  }

  function close(): void {
    query.value = null
  }

  function select(agent: MentionableAgent): void {
    const el = textarea.value
    if (!el) return
    const value = el.value
    const caret = el.selectionStart ?? value.length
    const info = activeMention(value, caret)
    if (!info) {
      close()
      return
    }
    const before = value.slice(0, info.start)
    const insert = `@${agent.name} `
    onInsert(before + insert + value.slice(caret))
    close()
    // Restore the caret just past the inserted mention.
    void nextTick(() => {
      const pos = before.length + insert.length
      el.focus()
      el.setSelectionRange(pos, pos)
    })
  }

  /** Handle a keydown while the list is open. Returns true when the key was
   *  consumed for navigation/selection so the caller skips its own handling. */
  function handleKeydown(e: KeyboardEvent): boolean {
    if (!open.value) return false
    const len = matches.value.length
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      activeIndex.value = (activeIndex.value + 1) % len
      return true
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      activeIndex.value = (activeIndex.value - 1 + len) % len
      return true
    }
    if (e.key === 'Escape') {
      e.preventDefault()
      close()
      return true
    }
    if ((e.key === 'Enter' && !e.shiftKey) || e.key === 'Tab') {
      e.preventDefault()
      select(matches.value[activeIndex.value]!)
      return true
    }
    return false
  }

  function handleKeyup(e: KeyboardEvent): void {
    // While navigating the open list, don't recompute (it would reset the
    // highlight); otherwise track the caret so moving into an @token reopens it.
    if (open.value && MENTION_NAV_KEYS.includes(e.key)) return
    refresh()
  }

  return { open, matches, activeIndex, refresh, close, select, handleKeydown, handleKeyup }
}
