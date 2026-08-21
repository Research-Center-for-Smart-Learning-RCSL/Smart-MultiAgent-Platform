// Copy text to the system clipboard, with a transient "copied" flag for the
// button that triggered it.
//
// Three failure modes have to be handled separately, and a bare
// `navigator.clipboard.writeText(...)` handles none of them:
//   * the API is absent entirely on a non-secure origin (and in jsdom), so the
//     property access itself is what has to be guarded;
//   * the promise rejects when the document has no user activation or the
//     permission is denied;
//   * a rejection has no user-visible consequence unless the caller is told,
//     which is why `copy` resolves a boolean instead of swallowing it.
//
// Callers own the message: the three slices that hand-rolled this each toasted
// with their own key, and one of them toasted "Loading..." on failure.

import { onScopeDispose, ref, type Ref } from 'vue'

const COPIED_RESET_MS = 2_000

export interface Clipboard {
  /** True for `resetMs` after a successful copy. Drives the button's state. */
  copied: Ref<boolean>
  /** Resolves false when the clipboard is unavailable or refused the write. */
  copy: (text: string) => Promise<boolean>
}

export function useClipboard(resetMs: number = COPIED_RESET_MS): Clipboard {
  const copied = ref(false)
  let timer: ReturnType<typeof setTimeout> | null = null

  function clearTimer(): void {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }

  async function copy(text: string): Promise<boolean> {
    copied.value = false
    clearTimer()
    if (typeof navigator === 'undefined' || !navigator.clipboard?.writeText) return false
    try {
      await navigator.clipboard.writeText(text)
    } catch {
      return false
    }
    copied.value = true
    timer = setTimeout(() => {
      copied.value = false
      timer = null
    }, resetMs)
    return true
  }

  // `failSilently`: the composable is usable outside a component scope (a test,
  // a plain module), where there is nothing to dispose and nothing to warn about.
  onScopeDispose(clearTimer, true)

  return { copied, copy }
}
