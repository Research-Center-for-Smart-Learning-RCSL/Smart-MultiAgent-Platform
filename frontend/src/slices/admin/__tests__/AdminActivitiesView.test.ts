// AC-5 / AC-6 for the admin activity governance view (R30.31). Requests go
// through MSW against the real endpoint paths, so the api wrapper and the
// generated client are exercised rather than stubbed.

import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import AdminActivitiesView from '../views/AdminActivitiesView.vue'

const TYPE_ROW = {
  id: 'at_1',
  project_id: 'proj_1',
  project_name: 'Creativity Study',
  key: 'mandala-9grid',
  name: '單元二 時空旅人',
  validator_kind: 'in_process',
  validator_config: { validator_id: 'filled_count', min_filled: 4 },
  expose_payload_to_agent: true,
  echo_includes_content: false,
  retention_days: null,
  version: 1,
  created_at: '2026-08-08T00:00:00Z',
}

const ACTIVATION_ROW = {
  id: 'act_1',
  chatroom_id: 'room_1',
  chatroom_name: 'Class 7A',
  activity_type_id: 'at_1',
  activity_type_key: 'mandala-9grid',
  activity_type_name: '單元二 時空旅人',
  started_by_user_id: 'u_1',
  created_at: '2026-08-08T01:00:00Z',
}

function stub(types: unknown[] = [], activations: unknown[] = []): void {
  server.use(
    http.get('/api/admin/activity-types', () => HttpResponse.json(types)),
    http.get('/api/admin/activity-activations', () => HttpResponse.json(activations)),
  )
}

async function settled() {
  await new Promise((r) => setTimeout(r, 50))
}

describe('AdminActivitiesView', () => {
  it('renders without errors', async () => {
    stub()
    const wrapper = await renderView(AdminActivitiesView)
    expect(wrapper.exists()).toBe(true)
  })

  it('lists types across projects with their governance flags (AC-5)', async () => {
    stub([TYPE_ROW], [])
    const wrapper = await renderView(AdminActivitiesView)
    await settled()

    expect(wrapper.text()).toContain('Creativity Study')
    expect(wrapper.text()).toContain('mandala-9grid')
    expect(wrapper.text()).toContain('單元二 時空旅人')
    // retention_days null renders as the room-default label, not a blank cell.
    expect(wrapper.text()).toContain('admin.activities.retentionUnset')
  })

  it('lists the activations that are running now (AC-5)', async () => {
    stub([], [ACTIVATION_ROW])
    const wrapper = await renderView(AdminActivitiesView)
    await settled()

    expect(wrapper.text()).toContain('Class 7A')
  })

  it('is read-only — no action buttons in either table (AC-5)', async () => {
    stub([TYPE_ROW], [ACTIVATION_ROW])
    const wrapper = await renderView(AdminActivitiesView)
    await settled()

    expect(wrapper.findAll('tbody button').length).toBe(0)
  })

  it('shows empty states when nothing is registered or running (AC-5)', async () => {
    stub([], [])
    const wrapper = await renderView(AdminActivitiesView)
    await settled()

    expect(wrapper.text()).toContain('admin.activities.typesEmpty')
    expect(wrapper.text()).toContain('admin.activities.activeEmpty')
  })

  it('surfaces a load error with a retry affordance (AC-5)', async () => {
    server.use(
      http.get('/api/admin/activity-types', () => HttpResponse.json({}, { status: 500 })),
      http.get('/api/admin/activity-activations', () => HttpResponse.json([])),
    )
    const wrapper = await renderView(AdminActivitiesView)
    await new Promise((r) => setTimeout(r, 300))

    expect(wrapper.text()).toContain('admin.common.loadError')
  })

  it('falls back to ids when a room or project name is missing (AC-5)', async () => {
    stub(
      [{ ...TYPE_ROW, project_name: null }],
      [{ ...ACTIVATION_ROW, chatroom_name: null, activity_type_name: null }],
    )
    const wrapper = await renderView(AdminActivitiesView)
    await settled()

    expect(wrapper.text()).toContain('proj_1')
    expect(wrapper.text()).toContain('room_1')
  })

  it('links each row into the audit view pre-filtered to that resource (AC-6)', async () => {
    stub([TYPE_ROW], [ACTIVATION_ROW])
    const wrapper = await renderView(AdminActivitiesView)
    await settled()

    const hrefs = wrapper.findAll('a').map((a) => a.attributes('href') ?? '')
    const typeLink = hrefs.find((h) => h.includes('resource_type=activity_type'))
    const activationLink = hrefs.find((h) => h.includes('resource_type=activity_activation'))

    expect(typeLink).toBeTruthy()
    expect(typeLink).toContain('resource_id=at_1')
    expect(activationLink).toBeTruthy()
    expect(activationLink).toContain('resource_id=act_1')
  })
})
