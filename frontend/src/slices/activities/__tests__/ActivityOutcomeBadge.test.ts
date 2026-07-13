// Outcome badge reflects backend status/is_valid only. The test i18n harness
// echoes keys, so assertions target the exact key rather than translated copy.

import { describe, expect, it } from 'vitest'
import { renderView } from '../../../../tests/utils'
import ActivityOutcomeBadge from '../components/ActivityOutcomeBadge.vue'
import type { ActivityValidationStatus } from '../types'

async function badge(status: ActivityValidationStatus, isValid: boolean | null) {
  return renderView(ActivityOutcomeBadge, { props: { status, isValid } })
}

describe('ActivityOutcomeBadge', () => {
  it('shows a validating state while pending', async () => {
    expect((await badge('pending', null)).text()).toContain('activities.outcome.pending')
  })

  it('shows valid / invalid from is_valid once validated', async () => {
    const valid = await badge('validated', true)
    expect(valid.text()).toContain('activities.outcome.valid')
    expect(valid.find('.s-badge--success').exists()).toBe(true)

    const invalid = await badge('validated', false)
    expect(invalid.text()).toContain('activities.outcome.invalid')
    expect(invalid.find('.s-badge--danger').exists()).toBe(true)
  })

  it('shows a neutral (not success) validated state when is_valid is still unknown', async () => {
    const wrapper = await badge('validated', null)
    expect(wrapper.text()).toContain('activities.outcome.validated')
    expect(wrapper.find('.s-badge--neutral').exists()).toBe(true)
    expect(wrapper.find('.s-badge--success').exists()).toBe(false)
  })

  it('shows an error state', async () => {
    expect((await badge('error', null)).text()).toContain('activities.outcome.error')
  })
})
