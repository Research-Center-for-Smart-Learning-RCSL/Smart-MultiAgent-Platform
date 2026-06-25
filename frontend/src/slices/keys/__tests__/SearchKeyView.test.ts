import { describe, it, expect, vi, beforeEach } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { renderView } from '../../../../tests/utils'

const mockConfirm = vi.hoisted(() => vi.fn(async () => false))
const mockToast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))

vi.mock('@shared/composables', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>
  return {
    ...actual,
    useConfirmDialog: () => ({ confirm: mockConfirm }),
    useToast: () => mockToast,
  }
})

vi.mock('../api/search-keys', () => ({
  searchKeysApi: {
    list: vi.fn(async () => ({
      data: [
        {
          id: 'sk_1',
          project_id: 'proj_1',
          provider: 'brave',
          masked_preview: 'BSQ...xyz',
          test_status: 'ok',
          test_error: null,
          last_test_at: '2026-01-15T10:30:00Z',
          is_active: true,
          config: {},
          created_at: '2026-01-01T00:00:00Z',
        },
        {
          id: 'sk_2',
          project_id: 'proj_1',
          provider: 'serper',
          masked_preview: 'SER...abc',
          test_status: 'failed',
          test_error: 'Invalid API key',
          last_test_at: '2026-01-10T08:00:00Z',
          is_active: false,
          config: {},
          created_at: '2026-01-02T00:00:00Z',
        },
        {
          id: 'sk_3',
          project_id: 'proj_1',
          provider: 'tavily',
          masked_preview: 'TAV...def',
          test_status: 'ok',
          test_error: null,
          last_test_at: null,
          is_active: false,
          config: { search_depth: 'advanced' },
          created_at: '2026-01-03T00:00:00Z',
        },
      ],
    })),
    upload: vi.fn(async () => ({ data: {} })),
    retest: vi.fn(async () => ({ data: {} })),
    activate: vi.fn(async () => ({ data: {} })),
    remove: vi.fn(async () => ({ data: {} })),
  },
}))

import SearchKeyView from '../views/SearchKeyView.vue'

const routes = [
  { path: '/projects/:projectId/search-keys', name: 'keys.searchKeys', component: SearchKeyView },
]

async function renderWithData() {
  const wrapper = await renderView(SearchKeyView, {
    routes,
    initialRoute: '/projects/proj_1/search-keys',
  })
  await flushPromises()
  await flushPromises()
  return wrapper
}

describe('SearchKeyView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders without errors', async () => {
    const wrapper = await renderView(SearchKeyView, {
      routes,
      initialRoute: '/projects/proj_1/search-keys',
    })
    expect(wrapper.exists()).toBe(true)
  })

  it('renders page header and add button', async () => {
    const wrapper = await renderView(SearchKeyView, {
      routes,
      initialRoute: '/projects/proj_1/search-keys',
    })
    const text = wrapper.text()
    expect(text).toContain('keys.search.title')
    expect(text).toContain('keys.search.add')
  })

  it('uses a single radio group with only the active key checked', async () => {
    const wrapper = await renderWithData()

    const radios = wrapper.findAll('input[type="radio"]')
    expect(radios.length).toBe(3)

    for (const radio of radios) {
      expect(radio.attributes('name')).toBe('active-search-key')
    }

    const checked = radios.filter((r) => (r.element as HTMLInputElement).checked)
    expect(checked).toHaveLength(1)

    const activeRadio = wrapper.find('[data-testid="activate-sk_1"] input[type="radio"]')
    expect((activeRadio.element as HTMLInputElement).checked).toBe(true)
  })

  it('shows "never" text when last_test_at is null', async () => {
    const wrapper = await renderWithData()

    const text = wrapper.text()
    expect(text).toContain('keys.search.never')
  })

  it('disables the radio for keys with failed test_status', async () => {
    const wrapper = await renderWithData()

    const failedRadio = wrapper.find('[data-testid="activate-sk_2"] input[type="radio"]')
    expect(failedRadio.exists()).toBe(true)
    expect(failedRadio.attributes('disabled')).toBeDefined()

    const okRadio = wrapper.find('[data-testid="activate-sk_1"] input[type="radio"]')
    expect(okRadio.attributes('disabled')).toBeUndefined()
  })

  it('includes deleteActiveWarning when deleting the active key', async () => {
    const wrapper = await renderWithData()

    const dropdowns = wrapper.findAllComponents({ name: 'SDropdown' })
    expect(dropdowns.length).toBe(3)

    dropdowns[0].vm.$emit('select', 'delete')
    await flushPromises()

    expect(mockConfirm).toHaveBeenCalledWith(
      expect.objectContaining({
        message: expect.stringContaining('keys.search.deleteActiveWarning'),
      }),
    )
  })
})
