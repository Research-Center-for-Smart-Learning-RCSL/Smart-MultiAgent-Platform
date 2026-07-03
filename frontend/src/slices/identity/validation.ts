import { z } from 'zod'
import type { Ref } from 'vue'

export const PASSWORD_MIN_LENGTH = 10

export const emailSchema = z.string()
  .min(1, 'identity.validation.emailRequired')
  .email('identity.validation.emailFormat')

export const passwordSchema = z.string()
  .min(1, 'identity.validation.passwordRequired')
  .min(PASSWORD_MIN_LENGTH, 'identity.validation.passwordMinLength')

export const DISPLAY_NAME_MAX_LENGTH = 50

// Optional label: an empty string is valid (it clears the name); only the
// upper bound is enforced client-side, mirroring the server cap.
export const displayNameSchema = z.string()
  .max(DISPLAY_NAME_MAX_LENGTH, 'identity.validation.displayNameMaxLength')

type TranslateFn = (key: string) => string

export function validateField(
  schema: z.ZodString,
  value: string,
  fieldErrors: Ref<Record<string, string | undefined>>,
  fieldKey: string,
  t: TranslateFn,
): boolean {
  const result = schema.safeParse(value)
  if (!result.success) {
    // zod guarantees at least one issue on a failed safeParse; guard anyway
    // rather than asserting, per noUncheckedIndexedAccess.
    const issue = result.error.issues[0]
    if (issue) {
      fieldErrors.value[fieldKey] = t(issue.message)
    }
    return false
  }
  fieldErrors.value[fieldKey] = undefined
  return true
}

// SFormField/SInput/SCheckbox declare their `error`/`disabled` props as
// optional (`?:`) rather than `| undefined`; exactOptionalPropertyTypes
// forbids passing an explicit `undefined` value for those. Build a v-bind
// object that omits the key entirely when there's nothing to show, e.g.
// `v-bind="errorAttrs(fieldErrors.email)"`.
export function errorAttrs(message: string | undefined): { error?: string } {
  return message !== undefined ? { error: message } : {}
}

export function validatePasswordMatch(
  password: string,
  confirm: string,
  fieldErrors: Ref<Record<string, string | undefined>>,
  fieldKey: string,
  t: TranslateFn,
): boolean {
  if (!confirm) {
    fieldErrors.value[fieldKey] = t('identity.validation.passwordRequired')
    return false
  }
  if (confirm !== password) {
    fieldErrors.value[fieldKey] = t('identity.validation.passwordMismatch')
    return false
  }
  fieldErrors.value[fieldKey] = undefined
  return true
}
