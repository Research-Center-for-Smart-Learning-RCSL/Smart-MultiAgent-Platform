// The platform-policy refusal names its offending switch with a wire identifier
// (`expose_payload_to_agent`). Dropping that straight into a sentence leaves the
// one word the reader most needs untranslated — 「原因是它的『expose_payload_to_agent』
// 設定」 — which defeats the reason the backend sends it as a structured member
// rather than as prose.

import { describe, expect, it } from 'vitest'
import { defineComponent } from 'vue'

import { ApiError } from '@shared/errors'

import { renderView } from '../../../../tests/utils'
import { usePolicyRefusal } from '../composables/usePolicyRefusal'

type Refusal = ReturnType<typeof usePolicyRefusal>

/** Mount the composable in a component so `useI18n()` has an instance. */
async function refusal(): Promise<Refusal> {
  let api!: Refusal
  const Host = defineComponent({
    setup() {
      api = usePolicyRefusal()
      return () => null
    },
  })
  await renderView(Host)
  return api
}

function policyError(extra: Record<string, unknown> = {}): ApiError {
  return new ApiError({
    type: 'https://smap.invalid/problems/activities/type-violates-policy',
    title: 'This activity type conflicts with the platform activity policy',
    status: 409,
    detail: 'platform policy requires expose_payload_to_agent to be false',
    ...extra,
  })
}

describe('usePolicyRefusal', () => {
  it('recognises the policy refusal', async () => {
    const { isPolicyRefusal } = await refusal()

    expect(isPolicyRefusal(policyError())).toBe(true)
  })

  it('ignores any other error, including another 409', async () => {
    const { isPolicyRefusal } = await refusal()

    expect(
      isPolicyRefusal(
        new ApiError({
          type: 'https://smap.invalid/problems/activities/already-active',
          title: 'A different activity is already active',
          status: 409,
        }),
      ),
    ).toBe(false)
    expect(isPolicyRefusal(new Error('boom'))).toBe(false)
  })

  it.each([
    ['expose_payload_to_agent', 'activities.typeForm.exposePayloadToAgent'],
    ['echo_includes_content', 'activities.typeForm.echoIncludesContent'],
    ['retention_days', 'activities.typeForm.retentionDays'],
  ])('translates %s through the label this UI already shows', async (field, key) => {
    const { refusedFieldLabel } = await refusal()

    expect(refusedFieldLabel(policyError({ field }))).toBe(key)
  })

  it('covers every field the backend policy gate can refuse', async () => {
    // Mirrors `_EXPOSE`/`_ECHO`/`_RETENTION` in
    // backend/contexts/activities/application/policy_service.py. A new gate
    // there without a label here silently degrades to the field-less message.
    const { refusedFieldLabel } = await refusal()
    const gated = ['expose_payload_to_agent', 'echo_includes_content', 'retention_days']

    for (const field of gated) {
      expect(refusedFieldLabel(policyError({ field }))).not.toBeNull()
    }
  })

  it('returns null rather than echoing a field it cannot translate', async () => {
    const { refusedFieldLabel } = await refusal()

    expect(refusedFieldLabel(policyError({ field: 'some_future_field' }))).toBeNull()
  })

  it('returns null when the refusal names no field', async () => {
    const { refusedFieldLabel } = await refusal()

    expect(refusedFieldLabel(policyError())).toBeNull()
  })
})
