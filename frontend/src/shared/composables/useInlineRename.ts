// Shared inline-rename state for the "click Rename → edit name → Save/Cancel"
// pattern used by org / project / key-group detail views. The view supplies a
// `save(name)` that performs the API call and refreshes its own state; this
// composable only owns the editing toggle + draft and closes the editor on a
// successful save (it stays open if `save` throws, so the view's error toast
// is visible and the user can retry).

import { ref, type Ref } from 'vue'

export interface InlineRename {
  renaming: Ref<boolean>
  nameDraft: Ref<string>
  start: () => void
  cancel: () => void
  save: () => Promise<void>
}

export function useInlineRename(opts: {
  current: () => string
  save: (name: string) => Promise<void>
}): InlineRename {
  const renaming = ref(false)
  const nameDraft = ref('')

  function start(): void {
    nameDraft.value = opts.current()
    renaming.value = true
  }
  function cancel(): void {
    renaming.value = false
  }
  async function save(): Promise<void> {
    const name = nameDraft.value.trim()
    if (!name) return
    try {
      await opts.save(name)
      renaming.value = false
    } catch {
      // Leave the editor open; opts.save is responsible for surfacing the error.
    }
  }

  return { renaming, nameDraft, start, cancel, save }
}
