import { describe, it, expect } from 'vitest'
import { useConfirmDialog } from '../useConfirmDialog'

describe('useConfirmDialog type-to-confirm gating', () => {
  it('blocks confirm until the typed value matches the pattern', () => {
    const { canConfirm, prompt, state, handleCancel } = useConfirmDialog()
    void prompt({
      title: 'Delete',
      message: 'Type DELETE to confirm',
      inputPattern: /^DELETE$/,
      variant: 'error',
    })

    expect(state.promptMode).toBe(true)
    expect(canConfirm.value).toBe(false)

    state.inputValue = 'DEL'
    expect(canConfirm.value).toBe(false)

    state.inputValue = 'DELETE'
    expect(canConfirm.value).toBe(true)

    handleCancel()
  })

  it('always allows confirm for a plain (non-prompt) dialog', () => {
    const { canConfirm, confirm, handleCancel } = useConfirmDialog()
    void confirm({ title: 'Restore', message: 'Restore this item?' })
    expect(canConfirm.value).toBe(true)
    handleCancel()
  })
})
