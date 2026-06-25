// Composable: debounced KaTeX/Mermaid/highlight post-processing of rendered
// markdown (FE-12). Extracted from ChatroomView (SRP).
//
// `onUpdated` fires on every reactive change and the Mermaid pass is async, so
// a burst is collapsed into one pass and overlapping runs are serialised; a
// change arriving mid-pass queues exactly one follow-up. An optional `onAfter`
// hook lets the host run cheap post-update work (e.g. re-pin scroll).

import { onBeforeUnmount, onMounted, onUpdated } from 'vue'
import type { Ref } from 'vue'

import { enhanceRenderedMarkdown } from '../utils/renderMarkdown'

const ENHANCE_DEBOUNCE_MS = 120

export function useMarkdownEnhance(
  rootRef: Readonly<Ref<HTMLElement | null>>,
  opts: { onAfterUpdate?: () => void } = {},
) {
  let timer: ReturnType<typeof setTimeout> | null = null
  let inFlight = false
  let queued = false

  async function run(): Promise<void> {
    if (inFlight) {
      queued = true
      return
    }
    if (!rootRef.value) return
    inFlight = true
    try {
      await enhanceRenderedMarkdown(rootRef.value)
    } catch {
      // Best-effort; rendering errors must not crash the chatroom.
    } finally {
      inFlight = false
    }
    if (queued) {
      queued = false
      schedule()
    }
  }

  function schedule(): void {
    if (timer !== null) clearTimeout(timer)
    timer = setTimeout(() => {
      timer = null
      void run()
    }, ENHANCE_DEBOUNCE_MS)
  }

  onMounted(schedule)
  onUpdated(() => {
    schedule()
    opts.onAfterUpdate?.()
  })
  onBeforeUnmount(() => {
    if (timer !== null) clearTimeout(timer)
  })
}
