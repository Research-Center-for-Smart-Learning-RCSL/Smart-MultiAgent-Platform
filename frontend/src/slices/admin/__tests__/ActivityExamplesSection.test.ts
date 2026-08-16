// AC-3/AC-8: the platform admin's install surface and the four-field edit.
//
// Requests go through MSW against the real endpoint paths, matching the sibling
// admin view tests, so the api wrapper and the generated client are exercised
// rather than stubbed.

import { describe, it, expect, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { flushPromises } from '@vue/test-utils'
import { QueryClient } from '@tanstack/vue-query'

import { server } from '../../../../tests/mocks/server'
import { renderView, deferred } from '../../../../tests/utils'
import ActivityExamplesSection from '../components/ActivityExamplesSection.vue'
import PlatformActivityTypeDialog from '../components/PlatformActivityTypeDialog.vue'

// One shared spy rather than a fresh object per call: the guard tests below
// assert that a refused submit *says* something, which a per-call mock cannot
// observe.
const { toast } = vi.hoisted(() => ({
  toast: { success: vi.fn(), error: vi.fn(), info: vi.fn(), warning: vi.fn() },
}))

vi.mock('@shared/composables', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  useToast: () => toast,
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

/** The paged cross-project listing. The section no longer reads it — the whole
 *  point of the platform route below — so this only appears where a test needs
 *  to prove the section is *not* resolving from it. */
function stubTypes(rows: unknown[]): ReturnType<typeof http.get> {
  return http.get('/api/admin/activity-types', () => HttpResponse.json(rows))
}

function stubPlatformTypes(rows: unknown[]): ReturnType<typeof http.get> {
  return http.get('/api/admin/platform-activity-types', () => HttpResponse.json(rows))
}

async function settled() {
  await new Promise((r) => setTimeout(r, 50))
}

describe('ActivityExamplesSection', () => {
  it('lists a shipped course and flags what it sends to the AI (AC-3)', async () => {
    server.use(
      http.get('/api/admin/activity-examples', () => HttpResponse.json([COURSE])),
      stubPlatformTypes([]),
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
      stubPlatformTypes([]),
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
      stubPlatformTypes([storedRow()]),
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
      stubPlatformTypes([storedRow({ name: 'Mandala (renamed by an admin)' })]),
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
      stubPlatformTypes([storedRow()]),
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
      stubPlatformTypes([storedRow()]),
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

  it('opens the edit form with the stored values even when the type is not in the paged types listing (AC-1/AC-2)', async () => {
    let body: Record<string, unknown> | null = null
    server.use(
      http.get('/api/admin/activity-examples', () => HttpResponse.json([installed('at_1')])),
      // The trap this test exists for: platform types are installed at setup, so
      // they are the oldest rows and the first to age off a newest-first page.
      // Resolving the edit target from here found nothing and opened a blank
      // form that saved silently.
      stubTypes(Array.from({ length: 200 }, (_, i) => storedRow({ id: `other_${i}` }))),
      stubPlatformTypes([
        storedRow({ name: 'Mandala (renamed by an admin)', expose_payload_to_agent: false }),
      ]),
      http.patch('/api/admin/activity-types/:typeId', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>
        return HttpResponse.json({})
      }),
    )

    const wrapper = await renderView(ActivityExamplesSection)
    await settled()

    await wrapper.find('[data-testid="edit-mandala-9grid"]').trigger('click')
    await flushPromises()

    const name = wrapper.find('[data-testid="platform-type-name"]')
    expect((name.element as HTMLInputElement).value).toBe('Mandala (renamed by an admin)')

    await wrapper.find('form').trigger('submit')
    await settled()

    expect(body).not.toBeNull()
    // The stored value, not PlatformActivityTypeDialog's `true` default for a
    // row it could not resolve.
    expect(body!.expose_payload_to_agent).toBe(false)
  })

  it('disables Edit until the stored row has resolved (AC-3)', async () => {
    const gate = deferred<void>()
    server.use(
      http.get('/api/admin/activity-examples', () => HttpResponse.json([installed('at_1')])),
      http.get('/api/admin/platform-activity-types', async () => {
        await gate.promise
        return HttpResponse.json([storedRow()])
      }),
    )

    const wrapper = await renderView(ActivityExamplesSection)
    await settled()

    // Offered but unusable is the defect; honestly disabled is the in-flight
    // state, and it must not outlive the request.
    const edit = () => wrapper.find('[data-testid="edit-mandala-9grid"]')
    expect(edit().attributes('disabled')).toBeDefined()

    gate.resolve()
    await settled()

    expect(edit().attributes('disabled')).toBeUndefined()
  })

  it('says so when an installed example has no stored row to edit (AC-9)', async () => {
    server.use(
      http.get('/api/admin/activity-examples', () => HttpResponse.json([installed('at_1')])),
      stubPlatformTypes([]),
    )

    const wrapper = await renderView(ActivityExamplesSection)
    await settled()

    expect(wrapper.text()).toContain('admin.activities.examples.storedRowUnavailable')
    expect(wrapper.find('[data-testid="edit-mandala-9grid"]').attributes('disabled')).toBeDefined()
  })

  it('shows an installed unit its stored values, not the shipped course file (AC-6)', async () => {
    server.use(
      http.get('/api/admin/activity-examples', () => HttpResponse.json([installed('at_1')])),
      stubPlatformTypes([
        storedRow({ name: 'Mandala (renamed by an admin)', expose_payload_to_agent: false }),
      ]),
    )

    const wrapper = await renderView(ActivityExamplesSection)
    await settled()

    // The catalogue card is built from the course file, which stops being the
    // truth the moment an admin edits the installed type.
    expect(wrapper.text()).toContain('Mandala (renamed by an admin)')
    expect(wrapper.text()).not.toContain('admin.activities.examples.exposesToAgent')
  })

  /** Open the edit form, type into it, then force a refetch of every admin
   *  query — what `refetchOnWindowFocus` does when an admin alt-tabs away and
   *  back. Both endpoints are stubbed so the row resolves whichever one the
   *  section reads; otherwise this would pass for the wrong reason, never
   *  having had a row to reseed from. */
  async function dirtyFormThroughRefetch(rows: () => unknown[]) {
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
    server.use(
      http.get('/api/admin/activity-examples', () => HttpResponse.json([installed('at_1')])),
      http.get('/api/admin/activity-types', () => HttpResponse.json(rows())),
      http.get('/api/admin/platform-activity-types', () => HttpResponse.json(rows())),
    )

    const wrapper = await renderView(ActivityExamplesSection, { queryClient: qc })
    await settled()

    await wrapper.find('[data-testid="edit-mandala-9grid"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="platform-type-name"]').setValue('half-typed name')

    await qc.invalidateQueries({ queryKey: ['admin'] })
    await settled()

    return (wrapper.find('[data-testid="platform-type-name"]').element as HTMLInputElement).value
  }

  it('does not reseed an open, dirty form when a refetch returns an identical row (AC-5)', async () => {
    expect(await dirtyFormThroughRefetch(() => [storedRow()])).toBe('half-typed name')
  })

  it('does not reseed an open, dirty form when the row itself changed under it (AC-5)', async () => {
    // The case that actually reaches the watch. vue-query's structural sharing
    // hands back the *previous* object when a refetch is deeply equal, so an
    // identical response never changes `row` identity — only a genuine edit
    // from elsewhere does, and that is when a reseed would silently discard
    // what this admin had typed.
    let call = 0
    const rows = () => [storedRow({ name: `Mandala (edit ${call++})` })]
    expect(await dirtyFormThroughRefetch(rows)).toBe('half-typed name')
  })
})

describe('PlatformActivityTypeDialog', () => {
  it('refuses to save an unresolved row out loud rather than silently (AC-4)', async () => {
    toast.warning.mockClear()
    let called = false
    server.use(
      http.patch('/api/admin/activity-types/:typeId', () => {
        called = true
        return HttpResponse.json({})
      }),
    )

    const wrapper = await renderView(PlatformActivityTypeDialog, {
      props: { open: true, row: null },
    })
    await flushPromises()

    await wrapper.find('[data-testid="platform-type-name"]').setValue('anything')
    await flushPromises()

    // Prevented first: with no row there is no id to PATCH, so Save cannot be
    // the thing that reports it.
    expect(wrapper.find('[data-testid="platform-type-save"]').attributes('disabled')).toBeDefined()

    await wrapper.find('form').trigger('submit')
    await settled()

    expect(called).toBe(false)
    expect(toast.warning).toHaveBeenCalled()
  })
})
