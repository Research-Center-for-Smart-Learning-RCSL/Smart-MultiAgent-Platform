import { describe, it, expect, vi } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../../../tests/mocks/server'
import { renderView } from '../../../../tests/utils'
import type * as SharedComposables from '@shared/composables'
import KnowledgeMapConfigListView from '../views/KnowledgeMapConfigListView.vue'

// Auto-confirm the delete dialog; keep every other shared composable intact.
vi.mock('@shared/composables', async (importOriginal) => {
  const actual = await importOriginal<typeof SharedComposables>()
  return { ...actual, useConfirmDialog: () => ({ confirm: async () => true }) }
})

const routes = [
  {
    path: '/projects/:projectId/knowmap-configs',
    name: 'agents.knowmapConfigs',
    component: KnowledgeMapConfigListView,
  },
  {
    path: '/projects/:projectId/knowmap-configs/:configId',
    name: 'agents.knowmapConfig',
    component: { template: '<div />' },
  },
]

// The create schema validates builder_key_group_id as a UUID, so the fixture id
// has to be a real one for the form to submit.
const KEY_GROUP_ID = '11111111-1111-4111-8111-111111111111'

const KEY_GROUPS = [
  { id: KEY_GROUP_ID, project_id: 'proj_1', name: 'Primary', created_at: '2026-01-01T00:00:00Z' },
]

function cfg(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    id: 'cfg_1',
    project_id: 'proj_1',
    name: 'Handbook Map',
    builder_key_group_id: KEY_GROUP_ID,
    chunk_strategy: 'fixed',
    chunk_params: { chunk_size_tokens: 512, chunk_overlap_tokens: 64 },
    embed_provider: 'openai',
    embed_model: 'text-embedding-3-small',
    embed_dim: 1536,
    last_build_state: 'idle',
    last_build_at: '2026-01-02T00:00:00Z',
    last_build_error: null,
    created_at: '2026-01-01T00:00:00Z',
    deleted_at: null,
    ...over,
  }
}

function seed(opts: { configs?: unknown[]; keyGroups?: unknown[] } = {}): void {
  server.use(
    http.get('/api/projects/proj_1/knowmap-configs', () => HttpResponse.json(opts.configs ?? [])),
    http.get('/api/projects/proj_1/key-groups', () =>
      HttpResponse.json(opts.keyGroups ?? KEY_GROUPS),
    ),
  )
}

async function settle(wrapper: { vm: { $nextTick: () => Promise<void> } }): Promise<void> {
  await new Promise((r) => setTimeout(r, 100))
  await wrapper.vm.$nextTick()
}

async function render() {
  const wrapper = await renderView(KnowledgeMapConfigListView, {
    routes,
    initialRoute: '/projects/proj_1/knowmap-configs',
  })
  await settle(wrapper)
  return wrapper
}

describe('KnowledgeMapConfigListView', () => {
  it('lists configs with their embedding model', async () => {
    seed({ configs: [cfg(), cfg({ id: 'cfg_2', name: 'Policy Map' })] })
    const wrapper = await render()
    expect(wrapper.text()).toContain('Handbook Map')
    expect(wrapper.text()).toContain('Policy Map')
    expect(wrapper.text()).toContain('openai/text-embedding-3-small')
  })

  it('shows the pending placeholder when no embedding is resolved yet', async () => {
    seed({ configs: [cfg({ embed_provider: null, embed_model: null, embed_dim: null })] })
    const wrapper = await render()
    // Locale bundles aren't loaded in the harness, so keys render verbatim.
    expect(wrapper.text()).toContain('agents.knowmapList.embedPending')
  })

  it('filters the list by name', async () => {
    seed({ configs: [cfg(), cfg({ id: 'cfg_2', name: 'Policy Map' })] })
    const wrapper = await render()
    await wrapper.find('input').setValue('policy')
    await settle(wrapper)
    expect(wrapper.text()).toContain('Policy Map')
    expect(wrapper.text()).not.toContain('Handbook Map')
  })

  it('disables create when the project has no key group', async () => {
    seed({ configs: [cfg()], keyGroups: [] })
    const wrapper = await render()
    expect(wrapper.find('button.s-btn--primary').attributes('disabled')).toBeDefined()
  })

  it('creates a config with the first key group preselected', async () => {
    seed({ configs: [] })
    let body: Record<string, unknown> | null = null
    server.use(
      http.post('/api/projects/proj_1/knowmap-configs', async ({ request }) => {
        body = (await request.json()) as Record<string, unknown>
        return HttpResponse.json(cfg({ name: body.name }), { status: 201 })
      }),
    )
    const wrapper = await render()

    await wrapper.find('button.s-btn--primary').trigger('click')
    await settle(wrapper)

    const nameInput = wrapper.findAll('input[type="text"]').at(-1)!
    await nameInput.setValue('New Map')
    const buttons = wrapper.findAll('button.s-btn--primary')
    await buttons[buttons.length - 1]!.trigger('click')
    await settle(wrapper)

    expect(body).not.toBeNull()
    expect(body!.name).toBe('New Map')
    expect(body!.builder_key_group_id).toBe(KEY_GROUP_ID)
    expect(body!.chunk_strategy).toBe('fixed')
  })

  it('deletes a config through the row action menu', async () => {
    seed({ configs: [cfg()] })
    let deleted = ''
    server.use(
      http.delete('/api/knowmap-configs/:configId', ({ params }) => {
        deleted = params.configId as string
        return new HttpResponse(null, { status: 204 })
      }),
    )
    const wrapper = await render()

    await wrapper.find('.s-dropdown button').trigger('click')
    await settle(wrapper)
    const deleteItem = wrapper
      .findAll('.s-dropdown [role="menuitem"]')
      .find((el) => el.text().includes('common.delete') || el.text().includes('Delete'))!
    await deleteItem.trigger('click')
    await settle(wrapper)

    expect(deleted).toBe('cfg_1')
  })
})
