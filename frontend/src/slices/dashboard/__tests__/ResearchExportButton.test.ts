import { afterEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import ResearchExportButton from '../components/ResearchExportButton.vue'

vi.mock('@shared/composables', () => ({
  useToast: () => ({
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  }),
}))

const api = vi.hoisted(() => ({
  createResearchExport: vi.fn(),
  getResearchExportStatus: vi.fn(),
}))
vi.mock('../api/researchExport', () => api)

function ready(url: string) {
  return { job_id: 'j1', workspace_id: 'w1', status: 'ready', url, error: null }
}

const i18n = createI18n({
  legacy: false,
  locale: 'en',
  messages: {
    en: {
      dashboard: {
        researchExport: {
          button: 'Export Research Data',
          dialogTitle: 'Export Research Data',
          dialogDescription: 'Export de-identified data.',
          dateAfter: 'From date',
          dateBefore: 'To date',
          cancel: 'Cancel',
          export: 'Export',
          download: 'Download',
          expired: 'Expired',
          ready: 'Ready',
          error: 'Failed',
          timeout: 'Timeout',
          status: {
            queued: 'Preparing...',
            running: 'Generating...',
            ready: 'Done!',
            failed: 'Failed.',
          },
        },
      },
    },
  },
})

function mountButton() {
  return mount(ResearchExportButton, {
    props: { workspaceId: 'w1' },
    global: {
      plugins: [i18n],
      stubs: {
        SCard: { template: '<div><slot /></div>' },
        Teleport: true,
      },
    },
  })
}

describe('ResearchExportButton', () => {
  it('renders the export button', () => {
    const wrapper = mountButton()
    expect(wrapper.text()).toContain('Export Research Data')
  })

  it('opens dialog on click', async () => {
    const wrapper = mountButton()
    await wrapper.find('button').trigger('click')
    await nextTick()
    expect(wrapper.html()).toContain('Export Research Data')
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('never opens a popup; downloads a link fetched at click time', async () => {
    vi.useFakeTimers()
    const open = vi.spyOn(window, 'open')
    const downloadSpy = vi.fn()
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      downloadSpy(this.getAttribute('href'))
    })
    api.createResearchExport.mockResolvedValue({ job_id: 'j1', status: 'queued' })
    api.getResearchExportStatus.mockResolvedValueOnce(ready('/api/files/first.t'))
    const wrapper = mountButton()
    await wrapper.find('button').trigger('click')
    const exportButton = wrapper.findAll('button').find((b) => b.text() === 'Export')!
    await exportButton.trigger('click')
    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()

    expect(open).not.toHaveBeenCalled()
    expect(downloadSpy).not.toHaveBeenCalled()

    api.getResearchExportStatus.mockResolvedValueOnce(ready('/api/files/fresh.t'))
    await wrapper.find('[data-testid="research-export-download"]').trigger('click')
    await flushPromises()

    expect(downloadSpy).toHaveBeenCalledWith('/api/files/fresh.t')
  })
})