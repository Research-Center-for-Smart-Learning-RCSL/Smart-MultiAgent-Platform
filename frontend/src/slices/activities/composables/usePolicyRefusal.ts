import { useI18n } from 'vue-i18n'

import { ApiError } from '@shared/errors'

/**
 * The platform activity policy refuses a write or an activation with
 * `activities/type-violates-policy` and a `field` problem member naming the
 * governance switch at fault.
 *
 * Two surfaces hit this — the facilitator starting an activity, and the owner
 * editing the type — and each needs its own sentence, so this resolves the
 * *field label* only and leaves the sentence to the caller.
 */

/**
 * Wire identifier -> the label the UI already uses for that switch.
 *
 * The backend names the field as `expose_payload_to_agent`, which is the right
 * thing for an API to say and the wrong thing to drop into a sentence: a zh-TW
 * reader would get one untranslated identifier exactly where the message is
 * meant to tell them what to fix. Anything not listed here falls back to the
 * field-less message rather than echoing the raw name.
 *
 * Exhaustive over what `ActivityPolicyService.assert_allows` can raise.
 */
const FIELD_LABEL_KEYS: Readonly<Record<string, string>> = {
  expose_payload_to_agent: 'activities.typeForm.exposePayloadToAgent',
  echo_includes_content: 'activities.typeForm.echoIncludesContent',
  retention_days: 'activities.typeForm.retentionDays',
}

export function usePolicyRefusal() {
  const { t } = useI18n()

  /** Whether `err` is the policy refusal, as opposed to any other 409. */
  function isPolicyRefusal(err: unknown): err is ApiError {
    return err instanceof ApiError && err.type.includes('type-violates-policy')
  }

  /**
   * The translated label of the refused switch, or null when this is not a
   * policy refusal or the backend named a field this build does not know.
   * Callers must test `isPolicyRefusal` first to tell those two cases apart.
   */
  function refusedFieldLabel(err: unknown): string | null {
    if (!isPolicyRefusal(err)) return null
    const field = err.extra.field
    const key = typeof field === 'string' ? FIELD_LABEL_KEYS[field] : undefined
    return key ? t(key) : null
  }

  return { isPolicyRefusal, refusedFieldLabel }
}
