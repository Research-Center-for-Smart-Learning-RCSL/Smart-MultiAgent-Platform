import { toast } from 'vue-sonner'

// Per-type display durations (§12 Shared Patterns §9). vue-sonner takes a
// per-call `duration` that overrides the global <Toaster :duration> default, so
// each toast lives exactly as long as its severity warrants: errors linger
// longest, transient successes clear fastest.
export const TOAST_DURATION_MS = {
  success: 4_000,
  error: 6_000,
  warning: 5_000,
  info: 4_000,
} as const

export interface ToastOptions {
  /** Secondary line under the title — keep it a short human sentence, never a
   *  stack trace (§9). */
  description?: string
}

export function useToast() {
  return {
    success: (msg: string, opts?: ToastOptions) =>
      toast.success(msg, {
        duration: TOAST_DURATION_MS.success,
        description: opts?.description,
      }),
    error: (msg: string, opts?: ToastOptions) =>
      toast.error(msg, {
        duration: TOAST_DURATION_MS.error,
        description: opts?.description,
      }),
    warning: (msg: string, opts?: ToastOptions) =>
      toast.warning(msg, {
        duration: TOAST_DURATION_MS.warning,
        description: opts?.description,
      }),
    info: (msg: string, opts?: ToastOptions) =>
      toast.info(msg, {
        duration: TOAST_DURATION_MS.info,
        description: opts?.description,
      }),
  }
}
