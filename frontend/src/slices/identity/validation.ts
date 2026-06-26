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
    fieldErrors.value[fieldKey] = t(result.error.issues[0].message)
    return false
  }
  fieldErrors.value[fieldKey] = undefined
  return true
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
