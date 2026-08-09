// AC-3/AC-8: the platform admin's install surface and the four-field edit.
//
// Requests go through MSW against the real endpoint paths, matching the sibling
// admin view tests, so the api wrapper and the generated client are exercised
// rather than stubbed.

import { describe, it, expect, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { flushPromises } from '@vue/test-utils'

import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import ActivityExamplesSection from '../components/ActivityExamplesSection.vue'

vi.mock('@shared/composables', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useToast: () => ({ success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() }),
}))

const COURSE = {
  course_key: 'creative-thinking',
  title: 'Creative thinking',
  source: 'Ke Pei-jung',
  activity_types: [
    {
      key: 'mandala-9grid',
      name: 'Mandala',
      expose_payload_to_agent: true,
      echo_includes_content: false,
      retention_days: null,
      installed_type_id: null,
    },
  ],
  fully_installed: false,
}

function installed(typeId: string) {
  return {
    ...COURSE,
    fully_installed: true,
    activity_types: [{ ...COURSE.activity_types[0], installed_type_id: typeId }],
  }
}

/** The stored row the edit form seeds from. Its values deliberately differ from
 *  the catalogue's in the tests below, so a form that seeded from the shipped
 *  course file instead would fail rather than coincide. */
function storedRow(over: Record<string, unknown> = {}) {
  return {
    id: 'at_1',
    project_id: null,
    project_name: null,
    scope: 'platform',
    key: 'mandala-9grid',
    name: 'Mandala',
    validator_kind: 'in_process',
    validator_config: { validator_id: 'filled_count', min_filled: 4 },
    expose_payload_to_agent: true,
    echo_includes_content: false,
    retention_days: null,
    version: 1,
    created_at: '2026-08-09T00:00:00Z',
    ...over,
  }
}

function stubTypes(rows: unknown[]): ReturnType<typeof http.get> {
  return http.get('/api/admin/activity-types', () => HttpResponse.json(rows))
}

async function settled() {
  await new Promise((r) => setTimeout(r, 50))
}

describe('ActivityExamplesSection', () => {
  it('lists a shipped course and flags what it sends to the AI (AC-3)', async () => {
    server.use(
      http.get('/api/admin/activity-examples', () => HttpResponse.json([COURSE])),
      stubTypes([]),
    )

    const wrapper = await renderView(ActivityExamplesSection)
    await settled()

    expect(wrapper.text()).toContain('Creative thinking')
    expect(wrapper.text()).toContain('creative-thinking')
    expect(wrapper.text()).toContain('admin.activities.examples.exposesToAgent')
    expect(wrapper.text()).toContain('admin.activities.examples.notInstalled')
  })

  it('installs a course and reports what it created (AC-3)', async () => {
    const calls: string[] = []
    server.use(
      http.get('/api/admin/activity-examples', () => HttpResponse.json([COURSE])),
      stubTypes([]),
      http.post('/api/admin/activity-examples/:courseKey/install', ({ params }) => {
        calls.push(String(params.courseKey))
        return HttpResponse.json({
          course_key: 'creative-thinking',
          created: ['mandala-9grid'],
          already_present: [],
        })
      }),
    )

    const wrapper = await renderView(ActivityExamplesSection)
    await settled()

    await wrapper.find('[data-testid="install-creative-thinking"]').trigger('click')
    await settled()

    expect(calls).toEqual(['creative-thinking'])
  })

  it('offers no install once the course is fully installed', async () => {
    server.use(
      http.get('/api/admin/activity-examples', () => HttpResponse.json([installed('at_1')])),
      stubTypes([storedRow()]),
    )

    const wrapper = await renderView(ActivityExamplesSection)
    await settled()

    const button = wrapper.find('[data-testid="install-creative-thinking"]')
    expect(button.attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('admin.activities.examples.installed')
  })

  it('edits only the four permitted fields of an installed type (AC-8)', async () => {
    let body: Record<string, unknown> | null = null
    server.use(
      http.get('/api/admin/activity-examples', () => HttpResponse.json([installed('at_1')])),
      // Deliberately not the course file's name: the form must seed from the
      // stored row, which an admin may already have edited.
      stubTypes([storedRow({ name: 'Mandala (renamed by an admin)' })]),
      http.patch('/api/admin/activity-types/:typeId', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({})
      }),
    )

    const wrapper = await renderView(ActivityExamplesSection)
    await settled()

    await wrapper.find('[data-testid="edit-mandala-9grid"]').trigger('click')
    await flushPromises()

    // Pre-filled from the stored row, so an edit of one field does not blank the
    // other three.
    const name = wrapper.find('[data-testid="platform-type-name"]')
    expect((name.element as HTMLInputElement).value).toBe('Mandala (renamed by an admin)')

    // The testid lands on SCheckbox's root <label>; the control is inside it.
    await wrapper.find('[data-testid="platform-type-expose"] input').setValue(false)
    await wrapper.find('form').trigger('submit')
    await settled()

    expect(body).not.toBeNull()
    expect(Object.keys(body!).sort()).toEqual([
      'echo_includes_content',
      'expose_payload_to_agent',
      'name',
      'retention_days',
    ])
    expect(body!.expose_payload_to_agent).toBe(false)
  })

  it('surfaces a policy refusal naming the offending field (AC-9)', async () => {
    server.use(
      http.get('/api/admin/activity-examples', () => HttpResponse.json([installed('at_1')])),
      stubTypes([storedRow()]),
      http.patch('/api/admin/activity-types/:typeId', () =>
        HttpResponse.json(
          {
            type: 'activities/type-violates-policy',
            title: 'conflict',
            status: 409,
            field: 'expose_payload_to_agent',
          },
          { status: 409, headers: { 'content-type': 'application/problem+json' } },
        ),
      ),
    )

    const wrapper = await renderView(ActivityExamplesSection)
    await settled()

    await wrapper.find('[data-testid="edit-mandala-9grid"]').trigger('click')
    await flushPromises()
    await wrapper.find('form').trigger('submit')
    await settled()

    // Q-4 exists precisely so an admin can edit an example into compliance, so
    // the refusal has to say which switch refused rather than being a bare 409.
    const refusal = wrapper.find('[data-testid="platform-type-refusal"]')
    expect(refusal.exists()).toBe(true)
    expect(refusal.text()).toContain('admin.activities.examples.policyRefusedField')
  })

  it('rejects a non-integer retention before sending anything', async () => {
    let called = false
    server.use(
      http.get('/api/admin/activity-examples', () => HttpResponse.json([installed('at_1')])),
      stubTypes([storedRow()]),
      http.patch('/api/admin/activity-types/:typeId', () => {
        called = true
        return HttpResponse.json({})
      }),
    )

    const wrapper = await renderView(ActivityExamplesSection)
    await settled()

    await wrapper.find('[data-testid="edit-mandala-9grid"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="platform-type-retention"]').setValue('half a year')
    await flushPromises()

    expect(wrapper.find('[data-testid="platform-type-retention-invalid"]').exists()).toBe(true)
    await wrapper.find('form').trigger('submit')
    await settled()

    expect(called).toBe(false)
  })
})
