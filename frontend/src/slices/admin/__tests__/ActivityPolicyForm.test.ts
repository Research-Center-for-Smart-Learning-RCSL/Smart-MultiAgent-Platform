// AC-12 at the UI: If-Match is sent only when a policy exists, a 409 reloads
// rather than retries, and the impact preview writes nothing.

import { describe, it, expect, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import ActivityPolicyForm from '../components/ActivityPolicyForm.vue'

const SAVED_POLICY = {
  expose_payload_to_agent_default: true,
  expose_payload_to_agent_locked: false,
  echo_includes_content_default: false,
  echo_includes_content_locked: false,
  retention_days_default: null,
  retention_days_max: 365,
  version: 4,
  updated_at: '2026-08-09T00:00:00Z',
  updated_by_user_id: 'u_1',
}

const NEVER_SAVED = { ...SAVED_POLICY, version: 0, updated_at: null, updated_by_user_id: null }

async function settled(ms = 60) {
  await new Promise((r) => setTimeout(r, ms))
}

describe('ActivityPolicyForm', () => {
  it('renders without errors', async () => {
    server.use(http.get('/api/admin/activity-policy', () => HttpResponse.json(NEVER_SAVED)))
    const wrapper = await renderView(ActivityPolicyForm)
    expect(wrapper.exists()).toBe(true)
  })

  it('says so when no policy has ever been saved', async () => {
    server.use(http.get('/api/admin/activity-policy', () => HttpResponse.json(NEVER_SAVED)))
    const wrapper = await renderView(ActivityPolicyForm)
    await settled()

    expect(wrapper.find('[data-testid="policy-unsaved"]').exists()).toBe(true)
  })

  it('omits If-Match on the first write and sends it once a policy exists', async () => {
    const seen: Array<string | null> = []
    server.use(
      http.get('/api/admin/activity-policy', () => HttpResponse.json(NEVER_SAVED)),
      http.put('/api/admin/activity-policy', ({ request }) => {
        seen.push(request.headers.get('If-Match'))
        return HttpResponse.json(SAVED_POLICY)
      }),
    )
    const first = await renderView(ActivityPolicyForm)
    await settled()
    await first.find('form').trigger('submit')
    await settled(120)

    // version 0 means "creating": there is nothing to match against.
    expect(seen[0]).toBeNull()

    server.use(http.get('/api/admin/activity-policy', () => HttpResponse.json(SAVED_POLICY)))
    const second = await renderView(ActivityPolicyForm)
    await settled()
    await second.find('form').trigger('submit')
    await settled(120)

    expect(seen[1]).toBe('4')
  })

  it('hydrates the form from the stored policy', async () => {
    server.use(
      http.get('/api/admin/activity-policy', () =>
        HttpResponse.json({ ...SAVED_POLICY, retention_days_default: 90 }),
      ),
    )
    const wrapper = await renderView(ActivityPolicyForm)
    await settled()

    const def = wrapper.find('[data-testid="policy-retention-default"]')
      .element as HTMLInputElement
    const max = wrapper.find('[data-testid="policy-retention-max"]').element as HTMLInputElement
    expect(def.value).toBe('90')
    expect(max.value).toBe('365')
  })

  it('previews impact without writing', async () => {
    let puts = 0
    server.use(
      http.get('/api/admin/activity-policy', () => HttpResponse.json(SAVED_POLICY)),
      http.post('/api/admin/activity-policy/impact', () =>
        HttpResponse.json({ violating_types: 7, approximate: false }),
      ),
      http.put('/api/admin/activity-policy', () => {
        puts += 1
        return HttpResponse.json(SAVED_POLICY)
      }),
    )
    const wrapper = await renderView(ActivityPolicyForm)
    await settled()

    await wrapper.find('[data-testid="policy-preview"]').trigger('click')
    await settled(120)

    expect(wrapper.find('[data-testid="policy-impact"]').text()).toContain(
      'admin.activities.policy.impact',
    )
    expect(puts).toBe(0)
  })

  it('reloads rather than retries when the policy changed underneath (409)', async () => {
    let gets = 0
    server.use(
      http.get('/api/admin/activity-policy', () => {
        gets += 1
        return HttpResponse.json(SAVED_POLICY)
      }),
      // problem+json, as the backend's error mapper actually returns — an empty
      // body would not construct an ApiError and the 409 branch would never run.
      http.put('/api/admin/activity-policy', () =>
        HttpResponse.json(
          {
            type: 'activities/policy-version-mismatch',
            title: 'The activity policy changed since this form was loaded',
            status: 409,
          },
          { status: 409, headers: { 'Content-Type': 'application/problem+json' } },
        ),
      ),
    )
    const wrapper = await renderView(ActivityPolicyForm)
    await settled()
    const before = gets

    await wrapper.find('form').trigger('submit')
    await settled(300)

    // The conflict handler invalidates the query, so the form reloads rather
    // than leaving a stale version to be retried against.
    expect(gets).toBeGreaterThan(before)
  })

  it('surfaces a load error with a retry affordance', async () => {
    server.use(
      http.get('/api/admin/activity-policy', () => HttpResponse.json({}, { status: 500 })),
    )
    const wrapper = await renderView(ActivityPolicyForm)
    await settled(300)

    expect(wrapper.text()).toContain('admin.common.loadError')
  })

  it('clears a retention bound back to unset rather than coercing it to 0', async () => {
    // SInput coerces an emptied type="number" to 0 via Number(''); 0 is not a
    // legal bound, so these are text inputs.
    let body: Record<string, unknown> | null = null
    server.use(
      http.get('/api/admin/activity-policy', () => HttpResponse.json(SAVED_POLICY)),
      http.put('/api/admin/activity-policy', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(SAVED_POLICY)
      }),
    )
    const wrapper = await renderView(ActivityPolicyForm)
    await settled()

    await wrapper.find('[data-testid="policy-retention-max"]').setValue('')
    await wrapper.find('form').trigger('submit')
    await settled(120)

    expect(body).not.toBeNull()
    expect(body!.retention_days_max).toBeNull()
  })

  it('does not fire a preview request on mount', async () => {
    const impact = vi.fn(() => HttpResponse.json({ violating_types: 0, approximate: false }))
    server.use(
      http.get('/api/admin/activity-policy', () => HttpResponse.json(SAVED_POLICY)),
      http.post('/api/admin/activity-policy/impact', impact),
    )
    await renderView(ActivityPolicyForm)
    await settled()

    expect(impact).not.toHaveBeenCalled()
  })
})
