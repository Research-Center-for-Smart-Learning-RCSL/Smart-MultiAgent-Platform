// Composable: per-agent streaming-draft bubbles. Memoised so only the agent
// whose text changed gets re-rendered — renderMarkdown + DOMPurify is expensive
// at token frequency. Extracted from ChatroomView (SRP).

import { computed } from 'vue'

import { renderMarkdown } from '../utils/renderMarkdown'
import { useConversationStore } from '../stores/conversation'

export function useAgentStreams(chatroomId: string) {
  const store = useConversationStore()
  const cache = new Map<string, { source: string; html: string }>()

  const streamingEntries = computed<[string, string][]>(() => {
    const roomStreams = store.agentStreams[chatroomId]
    if (!roomStreams) {
      cache.clear()
      return []
    }
    const activeIds = new Set<string>()
    const entries: [string, string][] = []
    for (const [agentId, text] of Object.entries(roomStreams)) {
      if (!text) continue
      activeIds.add(agentId)
      const cached = cache.get(agentId)
      if (cached && cached.source === text) {
        entries.push([agentId, cached.html])
      } else {
        const html = renderMarkdown(text)
        cache.set(agentId, { source: text, html })
        entries.push([agentId, html])
      }
    }
    for (const key of cache.keys()) {
      if (!activeIds.has(key)) cache.delete(key)
    }
    return entries
  })

  return { streamingEntries }
}
