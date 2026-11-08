import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { nextTick } from 'vue'
import { flushPromises, mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import { ApiError } from '@shared/errors'
import ResearchExportButton from '../components/ResearchExportButton.vue'

const toast = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))

vi.mock('@shared/composables', async () => ({
  useToast: () => toast,
  usePolling: (await import('@shared/composables/usePolling')).usePolling,
}))

const api = vi.hoisted(() => ({
  createResearchExport: vi.fn(),
  getLatestResearchExport: vi.fn(),
  getResearchExportStatus: vi.fn(),
}))
vi.mock('../api/researchExport', () => api)

function status(jobId: string, s: string, url: string | null = null) {
  return { job_id: jobId, workspace_id: 'w1', status: s, url, error: null }
}

function problem(status: number, type: string, extra: Record<string, unknown> = {}) {
  return new ApiError({ type: `https://smap.dev/problems${type}`, title: 'x', status, ...extra })
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
          close: 'Close',
          export: 'Export',
          download: 'Download',
          expired: 'Expired',
          ready: 'Ready',
          error: 'Failed',
          timeout: 'Timeout',
          checkAgain: 'Check again',
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

function mountButton(props: { openJobId?: string | null } = {}) {
  return mount(ResearchExportButton, {
    props: { workspaceId: 'w1', ...props },
    global: {
      plugins: [i18n],
      stubs: {
        SCard: { template: '<div><slot /></div>' },
        Teleport: true,
      },
    },
  })
}

const sel = (id: string) => `[data-testid="research-export-${id}"]`

describe('ResearchExportButton', () => {
  let downloads: string[]

  beforeEach(() => {
    vi.useFakeTimers()
    downloads = []
    vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
      downloads.push(this.getAttribute('href') ?? '')
    })
    api.getLatestResearchExport.mockResolvedValue(null)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.restoreAllMocks()
    vi.resetAllMocks()
  })

  it('renders the export button and opens the dialog', async () => {
    const wrapper = mountButton()
    expect(wrapper.text()).toContain('Export Research Data')
    await wrapper.find(sel('open')).trigger('click')
    await nextTick()
    expect(wrapper.find(sel('start')).exists()).toBe(true)
  })

  it('never opens a popup; downloads a link fetched at click time', async () => {
    const open = vi.spyOn(window, 'open')
    api.createResearchExport.mockResolvedValue({ job_id: 'j1', status: 'queued' })
    api.getResearchExportStatus.mockResolvedValueOnce(status('j1', 'ready', '/api/files/first.t'))
    const wrapper = mountButton()
    await wrapper.find(sel('open')).trigger('click')
    await flushPromises()
    await wrapper.find(sel('start')).trigger('click')
    await flushPromises()

    expect(toast.success).toHaveBeenCalledWith('Ready')
    expect(open).not.toHaveBeenCalled()
    expect(downloads).toEqual([])

    api.getResearchExportStatus.mockResolvedValueOnce(status('j1', 'ready', '/api/files/fresh.t'))
    await wrapper.find(sel('download')).trigger('click')
    await flushPromises()

    expect(downloads).toEqual(['/api/files/fresh.t'])
  })

  // AC-2: a job left running, or finished, while the dialog was closed.
  it('re-attaches to the latest ready job on open and offers Download', async () => {
    api.getLatestResearchExport.mockResolvedValue({ job_id: 'j7', status: 'ready' })
    api.getResearchExportStatus.mockResolvedValue(status('j7', 'ready', '/api/files/j7.t'))
    const wrapper = mountButton()
    await wrapper.find(sel('open')).trigger('click')
    await flushPromises()

    expect(api.getLatestResearchExport).toHaveBeenCalledWith('w1')
    expect(api.getResearchExportStatus).toHaveBeenCalledWith('j7')
    expect(wrapper.find(sel('download')).exists()).toBe(true)
    // Re-attaching to a job that was already done is not news.
    expect(toast.success).not.toHaveBeenCalled()
  })

  it('re-attaches to a running job and follows it to ready', async () => {
    api.getLatestResearchExport.mockResolvedValue({ job_id: 'j7', status: 'running' })
    api.getResearchExportStatus
      .mockResolvedValueOnce(status('j7', 'running'))
      .mockResolvedValueOnce(status('j7', 'ready', '/api/files/j7.t'))
    const wrapper = mountButton()
    await wrapper.find(sel('open')).trigger('click')
    await flushPromises()
    expect(wrapper.find(sel('progress')).text()).toContain('Generating...')

    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()
    expect(wrapper.find(sel('download')).exists()).toBe(true)
    expect(toast.success).toHaveBeenCalledWith('Ready')
  })

  it('shows the form when the latest job failed', async () => {
    api.getLatestResearchExport.mockResolvedValue({ job_id: 'j7', status: 'failed' })
    const wrapper = mountButton()
    await wrapper.find(sel('open')).trigger('click')
    await flushPromises()

    expect(api.getResearchExportStatus).not.toHaveBeenCalled()
    expect(wrapper.find(sel('start')).attributes('disabled')).toBeUndefined()
  })

  it('keeps following the job after the dialog closes', async () => {
    api.createResearchExport.mockResolvedValue({ job_id: 'j1', status: 'queued' })
    api.getResearchExportStatus
      .mockResolvedValueOnce(status('j1', 'queued'))
      .mockResolvedValueOnce(status('j1', 'ready', '/api/files/j1.t'))
    const wrapper = mountButton()
    await wrapper.find(sel('open')).trigger('click')
    await flushPromises()
    await wrapper.find(sel('start')).trigger('click')
    await flushPromises()
    await wrapper.find(sel('close')).trigger('click')
    expect(wrapper.find(sel('start')).exists()).toBe(false)

    await vi.advanceTimersByTimeAsync(3000)
    await flushPromises()
    expect(toast.success).toHaveBeenCalledWith('Ready')

    await wrapper.find(sel('open')).trigger('click')
    await flushPromises()
    expect(api.getLatestResearchExport).toHaveBeenCalledTimes(1)
    expect(wrapper.find(sel('download')).exists()).toBe(true)
  })

  // AC-3: the server allows one active export per owner and workspace.
  it('follows the running job named by a 409', async () => {
    api.createResearchExport.mockRejectedValue(
      problem(409, '/conversation/research-export-in-progress', { job_id: 'j9' }),
    )
    api.getResearchExportStatus.mockResolvedValue(status('j9', 'running'))
    const wrapper = mountButton()
    await wrapper.find(sel('open')).trigger('click')
    await flushPromises()
    await wrapper.find(sel('start')).trigger('click')
    await flushPromises()

    expect(api.getResearchExportStatus).toHaveBeenCalledWith('j9')
    expect(wrapper.find(sel('progress')).text()).toContain('Generating...')
    expect(toast.error).not.toHaveBeenCalled()
  })

  it('reports a create failure and returns to the form', async () => {
    api.createResearchExport.mockRejectedValue(problem(500, '/internal'))
    const wrapper = mountButton()
    await wrapper.find(sel('open')).trigger('click')
    await flushPromises()
    await wrapper.find(sel('start')).trigger('click')
    await flushPromises()

    expect(toast.error).toHaveBeenCalledWith('Failed')
    expect(wrapper.find(sel('start')).attributes('disabled')).toBeUndefined()
  })

  // AC-2: the `export.ready` notification links here with the job id.
  it('opens on the job id it is given', async () => {
    api.getResearchExportStatus.mockResolvedValue(status('j5', 'ready', '/api/files/j5.t'))
    const wrapper = mountButton({ openJobId: 'j5' })
    await flushPromises()

    expect(api.getResearchExportStatus).toHaveBeenCalledWith('j5')
    expect(api.getLatestResearchExport).not.toHaveBeenCalled()
    expect(wrapper.find(sel('download')).exists()).toBe(true)
  })

  it.each([404, 410])('reports an opened job answered with %i as expired', async (code) => {
    api.getResearchExportStatus.mockRejectedValue(problem(code, '/conversation/export-expired'))
    const wrapper = mountButton({ openJobId: 'j5' })
    await flushPromises()

    expect(toast.error).toHaveBeenCalledWith('Expired')
    expect(wrapper.find(sel('download')).exists()).toBe(false)
    expect(wrapper.find(sel('start')).attributes('disabled')).toBeUndefined()
  })

  it('treats a 410 at download time as expired', async () => {
    api.getResearchExportStatus
      .mockResolvedValueOnce(status('j5', 'ready', '/api/files/j5.t'))
      .mockRejectedValueOnce(problem(410, '/conversation/export-expired'))
    const wrapper = mountButton({ openJobId: 'j5' })
    await flushPromises()
    await wrapper.find(sel('download')).trigger('click')
    await flushPromises()

    expect(downloads).toEqual([])
    expect(toast.error).toHaveBeenCalledWith('Expired')
    expect(wrapper.find(sel('download')).exists()).toBe(false)
  })

  it('offers Check again when polling gives up, and resumes the same job', async () => {
    api.createResearchExport.mockResolvedValue({ job_id: 'j1', status: 'queued' })
    api.getResearchExportStatus.mockResolvedValue(status('j1', 'running'))
    const wrapper = mountButton()
    await wrapper.find(sel('open')).trigger('click')
    await flushPromises()
    await wrapper.find(sel('start')).trigger('click')
    await flushPromises()
    await vi.advanceTimersByTimeAsync(20 * 60 * 1000)
    await flushPromises()

    expect(wrapper.find(sel('stalled')).exists()).toBe(true)
    const calls = api.getResearchExportStatus.mock.calls.length

    api.getResearchExportStatus.mockResolvedValue(status('j1', 'ready', '/api/files/j1.t'))
    await wrapper.find(sel('check-again')).trigger('click')
    await flushPromises()

    expect(api.getResearchExportStatus.mock.calls.length).toBe(calls + 1)
    expect(api.getResearchExportStatus).toHaveBeenLastCalledWith('j1')
    expect(wrapper.find(sel('download')).exists()).toBe(true)
  })
})
