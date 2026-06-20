import { reactive } from 'vue'

export interface ConfirmOptions {
  title: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  variant?: 'warning' | 'error' | 'info'
}

export interface PromptOptions extends ConfirmOptions {
  inputPattern?: RegExp
  inputErrorMessage?: string
}

interface DialogState {
  open: boolean
  title: string
  message: string
  confirmLabel: string
  cancelLabel: string
  variant: 'warning' | 'error' | 'info'
  promptMode: boolean
  inputValue: string
  inputPattern: RegExp | null
  inputErrorMessage: string
  inputError: string
  resolve: ((value: boolean | string | null) => void) | null
}

const state: DialogState = reactive({
  open: false,
  title: '',
  message: '',
  confirmLabel: '',
  cancelLabel: '',
  variant: 'warning',
  promptMode: false,
  inputValue: '',
  inputPattern: null,
  inputErrorMessage: '',
  inputError: '',
  resolve: null,
})

function resetState() {
  state.open = false
  state.promptMode = false
  state.inputValue = ''
  state.inputPattern = null
  state.inputErrorMessage = ''
  state.inputError = ''
  state.resolve = null
}

function openDialog(options: ConfirmOptions) {
  state.title = options.title
  state.message = options.message
  state.confirmLabel = options.confirmLabel ?? ''
  state.cancelLabel = options.cancelLabel ?? ''
  state.variant = options.variant ?? 'warning'
  state.open = true
}

function confirm(options: ConfirmOptions): Promise<boolean> {
  return new Promise((resolve) => {
    openDialog(options)
    state.promptMode = false
    state.resolve = resolve as (value: boolean | string | null) => void
  })
}

function prompt(options: PromptOptions): Promise<string | null> {
  return new Promise((resolve) => {
    openDialog(options)
    state.promptMode = true
    state.inputValue = ''
    state.inputPattern = options.inputPattern ?? null
    state.inputErrorMessage = options.inputErrorMessage ?? ''
    state.inputError = ''
    state.resolve = resolve as (value: boolean | string | null) => void
  })
}

function handleConfirm() {
  if (state.promptMode) {
    if (state.inputPattern && !state.inputPattern.test(state.inputValue)) {
      state.inputError = state.inputErrorMessage
      return
    }
    state.resolve?.(state.inputValue)
  } else {
    state.resolve?.(true)
  }
  resetState()
}

function handleCancel() {
  state.resolve?.(state.promptMode ? null : false)
  resetState()
}

export function useConfirmDialog() {
  return { state, confirm, prompt, handleConfirm, handleCancel }
}
