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
    expect((await badge('validated', true)).text()).toContain('activities.outcome.valid')
    expect((await badge('validated', false)).text()).toContain('activities.outcome.invalid')
  })

  it('shows a neutral validated state when is_valid is still unknown', async () => {
    expect((await badge('validated', null)).text()).toContain('activities.outcome.validated')
  })

  it('shows an error state', async () => {
    expect((await badge('error', null)).text()).toContain('activities.outcome.error')
  })
})
