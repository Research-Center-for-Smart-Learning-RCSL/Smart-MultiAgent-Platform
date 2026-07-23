// AC-1 (owner gate) and AC-2 (list / empty). The api, the project-role gate,
// and the create form are mocked so the view is tested in isolation.

import { describe, expect, it, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'

import type * as TenancySlice from '@slices/tenancy'
import { renderView } from '../../../../tests/utils'
import ActivityTypesView from '../views/ActivityTypesView.vue'

const role = vi.hoisted(() => ({ decided: true, isAuthorized: true }))
const listMock = vi.hoisted(() => vi.fn())
const deleteMock = vi.hoisted(() => vi.fn())

vi.mock('../api', () => ({ listActivityTypes: listMock, deleteActivityType: deleteMock }))
vi.mock('@slices/tenancy', async (importOriginal) => {
  const { computed } = await import('vue')
  return {
    ...(await importOriginal<typeof TenancySlice>()),
    useProjectRole: () => ({
      decided: computed(() => role.decided),
      isAuthorized: computed(() => role.isAuthorized),
    }),
  }
})
vi.mock('../components/ActivityTypeForm.vue', () => ({
  default: { name: 'ActivityTypeForm', template: '<div data-testid="type-form-stub" />' },
}))

const routes = [{ path: '/at/:projectId', name: 'at', component: { template: '<div />' } }]

function mountView() {
  return renderView(ActivityTypesView, { routes, initialRoute: '/at/p1' })
}

beforeEach(() => {
  role.decided = true
  role.isAuthorized = true
  listMock.mockReset()
  deleteMock.mockReset()
})

describe('ActivityTypesView', () => {
  it('lists live types and shows the create action for an owner (AC-1, AC-2)', async () => {
    listMock.mockResolvedValue([
      {
        id: 't1',
        project_id: 'p1',
        key: 'quiz',
        name: 'Weekly Quiz',
        payload_schema: {},
        validator_kind: 'webhook',
        validator_config: {},
        retention_days: null,
        created_at: null,
      },
    ])

    const wrapper = await mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('Weekly Quiz')
    expect(wrapper.text()).toContain('quiz')
    expect(wrapper.text()).toContain('activities.typesList.create')
  })

  it('shows the empty state when there are no types (AC-2)', async () => {
    listMock.mockResolvedValue([])

    const wrapper = await mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('activities.typesList.emptyTitle')
  })

  it('shows an error state with a retry when the list fails to load (AC-2)', async () => {
    listMock.mockRejectedValue(new Error('boom'))

    const wrapper = await mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('activities.typesList.errorTitle')
    expect(wrapper.text()).toContain('activities.typesList.retry')
    expect(wrapper.text()).not.toContain('activities.typesList.emptyTitle')
  })

  it('hides the page and never queries for a non-owner (AC-1)', async () => {
    role.isAuthorized = false

    const wrapper = await mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('activities.typesList.forbiddenTitle')
    expect(wrapper.text()).not.toContain('activities.typesList.create')
    expect(listMock).not.toHaveBeenCalled()
  })
})
