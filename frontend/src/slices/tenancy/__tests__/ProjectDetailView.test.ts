import { describe, it, expect } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import ProjectDetailView from '../views/ProjectDetailView.vue'

const routes = [
  { path: '/projects/:id', name: 'tenancy.projectDetail', component: ProjectDetailView },
  { path: '/projects', name: 'tenancy.projectList', component: { template: '<div />' } },
  { path: '/projects/:id/members', name: 'tenancy.projectMembers', component: { template: '<div />' } },
  { path: '/projects/:projectId/skills', name: 'skills.project', component: { template: '<div />' } },
  { path: '/projects/:projectId/activity-types', name: 'activities.types', component: { template: '<div />' } },
  { path: '/orgs/:id', name: 'tenancy.orgDetail', component: { template: '<div />' } },
]

describe('ProjectDetailView', () => {
  it('renders without errors', async () => {
    const wrapper = await renderView(ProjectDetailView, {
      routes,
      initialRoute: '/projects/proj_1',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('displays project name after loading', async () => {
    const wrapper = await renderView(ProjectDetailView, {
      routes,
      initialRoute: '/projects/proj_1',
    })
    await flushPromises()
    expect(wrapper.text()).toContain('Test Project')
  })

  // F-26: the whole template sat behind the loading flag, page header included,
  // so a cold load painted nothing but a 24px spinner row at the top left. The
  // replacement is a header-shaped skeleton, not a header titled "Loading" —
  // the h1 on a detail page is the entity's name, and a status string in it is
  // both a false accessible name and a broken invariant (it took two e2e specs
  // down when this shipped that way).
  it('paints a header-shaped skeleton while the project is still loading', async () => {
    // Deliberately no flushPromises — the query is still in flight here.
    const wrapper = await renderView(ProjectDetailView, {
      routes,
      initialRoute: '/projects/proj_1',
    })
    expect(wrapper.find('.s-spinner').exists()).toBe(true)
    expect(wrapper.find('.s-skeleton').exists()).toBe(true)
    expect(wrapper.find('h1').exists()).toBe(false)
  })

  // The owner controls follow the server's verdict, not a scan of the member list:
  // an Org Owner holds no owner row (R5.03), and a bare org member is refused the
  // member list outright ([R5.07], AC-9), so it is not requested at all.
  function seedProject(project: Record<string, unknown>): { memberReads: () => number } {
    let reads = 0
    server.use(
      http.get('/api/projects/proj_1', () =>
        HttpResponse.json({ id: 'proj_1', name: 'Test Project', owner_type: 'org', owner_id: 'org_1', version: 1, created_at: '2026-01-01T00:00:00Z', deleted_at: null, ...project }),
      ),
      http.get('/api/projects/proj_1/members', () => {
        reads += 1
        return new HttpResponse(null, { status: 403 })
      }),
    )
    return { memberReads: () => reads }
  }

  it('offers a bare org member no owner control and does not read the member list', async () => {
    const calls = seedProject({ is_moderator: false, has_project_role: false })
    const wrapper = await renderView(ProjectDetailView, { routes, initialRoute: '/projects/proj_1' })
    await flushPromises()
    await flushPromises()

    expect(wrapper.text()).toContain('Test Project')
    expect(wrapper.find('a[href="/projects/proj_1/members"]').exists()).toBe(false)
    expect(calls.memberReads()).toBe(0)
  })

  it('offers an inherited owner the owner controls (FU-2)', async () => {
    seedProject({ is_moderator: true, has_project_role: true })
    const wrapper = await renderView(ProjectDetailView, { routes, initialRoute: '/projects/proj_1' })
    await flushPromises()
    await flushPromises()

    expect(wrapper.find('a[href="/projects/proj_1/members"]').exists()).toBe(true)
  })
})
