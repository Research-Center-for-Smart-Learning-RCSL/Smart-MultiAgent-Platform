import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'

// --- Mock data ---------------------------------------------------------

const defaultRotation = {
  rotate_on_error_codes: [429],
  rotate_on_token_quota: false,
  retry_on_error: true,
  retry_initial_delay_ms: 500,
  retry_multiplier: 2,
  retry_max_delay_ms: 30000,
  retry_max: 3,
  retry_jitter_pct: 10,
}
const defaultLimits = {
  max_input_tokens_per_hour: null,
  max_output_tokens_per_hour: null,
  max_requests_per_hour: null,
}

const carriedKeys = [
  { id: 'key_claude', provider: 'claude', name: 'Claude Prod', masked_preview: 'sk-***abc', test_status: 'ok', test_error: null, last_test_at: null, created_at: new Date().toISOString() },
  { id: 'key_openai', provider: 'openai', name: 'OpenAI Prod', masked_preview: 'sk-***def', test_status: 'ok', test_error: null, last_test_at: null, created_at: new Date().toISOString() },
  { id: 'key_voyage', provider: 'voyage', name: 'Voyage Embed', masked_preview: 'vk-***ghi', test_status: 'ok', test_error: null, last_test_at: null, created_at: new Date().toISOString() },
  { id: 'key_cohere', provider: 'cohere', name: 'Cohere Rerank', masked_preview: 'co-***jkl', test_status: 'ok', test_error: null, last_test_at: null, created_at: new Date().toISOString() },
]

const membersWithKeys = [
  { key_id: 'key_claude', priority: 1, rotation: { ...defaultRotation }, limits: { ...defaultLimits } },
  { key_id: 'key_openai', priority: 2, rotation: { ...defaultRotation }, limits: { ...defaultLimits } },
]

// --- Mocks (before component import) -----------------------------------

const mockGet = vi.fn()
const mockListCarried = vi.fn()

vi.mock('../api/key-groups', () => ({
  keyGroupsApi: {
    get: (...args: unknown[]) => mockGet(...args),
    addMember: vi.fn(async () => ({ data: {} })),
    removeMember: vi.fn(async () => ({ data: {} })),
    patchMember: vi.fn(async () => ({ data: {} })),
    reorder: vi.fn(async () => ({ data: {} })),
  },
}))

vi.mock('../api/project-keys', () => ({
  projectKeysApi: {
    listCarried: (...args: unknown[]) => mockListCarried(...args),
  },
}))

vi.mock('../api/keys', () => ({
  CAPABILITIES: {
    claude: ['llm_chat'],
    openai: ['llm_chat', 'embedding'],
    gemini: ['llm_chat', 'embedding'],
    voyage: ['embedding'],
    cohere: ['rerank'],
  },
}))

import KeyGroupDetailView from '../views/KeyGroupDetailView.vue'

const routes = [
  {
    path: '/projects/:projectId/key-groups/:id',
    name: 'keys.groupDetail',
    component: KeyGroupDetailView,
  },
]

async function flush() {
  await flushPromises()
  await flushPromises()
}

describe('KeyGroupDetailView', () => {
  beforeEach(() => {
    mockGet.mockReset()
    mockListCarried.mockReset()

    // Default: empty group, empty carried keys
    mockGet.mockResolvedValue({
      data: {
        group: { id: 'kg_1', project_id: 'proj_1', name: 'Group One', created_at: new Date().toISOString() },
        members: [],
      },
    })
    mockListCarried.mockResolvedValue({ data: [] })
  })

  it('renders without errors', async () => {
    const wrapper = await renderView(KeyGroupDetailView, {
      routes,
      initialRoute: '/projects/proj_1/key-groups/kg_1',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('shows add-member form controls', async () => {
    const wrapper = await renderView(KeyGroupDetailView, {
      routes,
      initialRoute: '/projects/proj_1/key-groups/kg_1',
    })
    await flush()
    expect(wrapper.find('[data-testid="add-member-select"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="add-member"]').exists()).toBe(true)
  })

  it('shows empty state when group has no members', async () => {
    mockGet.mockResolvedValue({
      data: {
        group: { id: 'kg_1', project_id: 'proj_1', name: 'Group One', created_at: new Date().toISOString() },
        members: [],
      },
    })
    mockListCarried.mockResolvedValue({ data: carriedKeys })

    const wrapper = await renderView(KeyGroupDetailView, {
      routes,
      initialRoute: '/projects/proj_1/key-groups/kg_1',
    })
    await flush()

    expect(wrapper.find('.s-empty-state').exists()).toBe(true)
    expect(wrapper.text()).toContain('keys.groups.noMembers')
  })

  it('filters add-member dropdown to llm_chat-capable keys only', async () => {
    mockGet.mockResolvedValue({
      data: {
        group: { id: 'kg_1', project_id: 'proj_1', name: 'Group One', created_at: new Date().toISOString() },
        members: [],
      },
    })
    mockListCarried.mockResolvedValue({ data: carriedKeys })

    const wrapper = await renderView(KeyGroupDetailView, {
      routes,
      initialRoute: '/projects/proj_1/key-groups/kg_1',
    })
    await flush()

    const select = wrapper.find('[data-testid="add-member-select"]')
    const options = select.findAll('option').filter((o) => o.attributes('value') !== '')

    const labels = options.map((o) => o.text())

    // claude and openai have llm_chat capability, so they appear
    expect(labels).toContain('claude - Claude Prod')
    expect(labels).toContain('openai - OpenAI Prod')

    // voyage (embedding-only) and cohere (rerank-only) must NOT appear
    expect(labels).not.toContain('voyage - Voyage Embed')
    expect(labels).not.toContain('cohere - Cohere Rerank')
  })

  it('renders member rows with key info from carriedKeyMap', async () => {
    mockGet.mockResolvedValue({
      data: {
        group: { id: 'kg_1', project_id: 'proj_1', name: 'Group One', created_at: new Date().toISOString() },
        members: membersWithKeys,
      },
    })
    mockListCarried.mockResolvedValue({ data: carriedKeys })

    const wrapper = await renderView(KeyGroupDetailView, {
      routes,
      initialRoute: '/projects/proj_1/key-groups/kg_1',
    })
    await flush()

    // Empty state should NOT be shown
    expect(wrapper.find('.s-empty-state').exists()).toBe(false)

    // Each member row is rendered with its data-testid
    const row1 = wrapper.find('[data-testid="member-key_claude"]')
    const row2 = wrapper.find('[data-testid="member-key_openai"]')
    expect(row1.exists()).toBe(true)
    expect(row2.exists()).toBe(true)

    // Priority badges
    expect(row1.text()).toContain('#1')
    expect(row2.text()).toContain('#2')

    // Key name from carriedKeyMap
    expect(row1.text()).toContain('Claude Prod')
    expect(row2.text()).toContain('OpenAI Prod')

    // Masked preview from carriedKeyMap
    expect(row1.text()).toContain('sk-***abc')
    expect(row2.text()).toContain('sk-***def')
  })
})
