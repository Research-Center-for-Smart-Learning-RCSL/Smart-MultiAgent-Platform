import { reactive, watch } from 'vue'
import { useRouter } from 'vue-router'

export type DialogVariant = 'warning' | 'error' | 'info'

export interface ConfirmOptions {
  title: string
  message: string
  confirmLabel?: string
  cancelLabel?: string
  variant?: DialogVariant
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
  variant: DialogVariant
  promptMode: boolean
  inputValue: string
  inputPattern: RegExp | null
  inputErrorMessage: string
  inputError: string
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
})

let pendingResolve: ((value: boolean | string | null) => void) | null = null

function dismissPending() {
  if (pendingResolve) {
    const prev = pendingResolve
    pendingResolve = null
    prev(state.promptMode ? null : false)
  }
}

function resetState() {
  state.open = false
  state.title = ''
  state.message = ''
  state.confirmLabel = ''
  state.cancelLabel = ''
  state.variant = 'warning'
  state.promptMode = false
  state.inputValue = ''
  state.inputPattern = null
  state.inputErrorMessage = ''
  state.inputError = ''
}

function openDialog(options: ConfirmOptions) {
  dismissPending()
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
    pendingResolve = resolve as (value: boolean | string | null) => void
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
    pendingResolve = resolve as (value: boolean | string | null) => void
  })
}

function handleConfirm() {
  if (state.promptMode) {
    if (state.inputPattern && !state.inputPattern.test(state.inputValue)) {
      state.inputError = state.inputErrorMessage
      return
    }
    pendingResolve?.(state.inputValue)
  } else {
    pendingResolve?.(true)
  }
  pendingResolve = null
  resetState()
}

function handleCancel() {
  pendingResolve?.(state.promptMode ? null : false)
  pendingResolve = null
  resetState()
}

let navGuardInstalled = false

export function useConfirmDialog() {
  if (!navGuardInstalled) {
    navGuardInstalled = true
    try {
      const router = useRouter()
      watch(() => router.currentRoute.value.path, () => {
        if (state.open) {
          dismissPending()
          resetState()
        }
      })
    } catch {
      // called outside component context (e.g. errorHandler) — no nav guard needed
    }
  }
  return { state, confirm, prompt, handleConfirm, handleCancel }
}
