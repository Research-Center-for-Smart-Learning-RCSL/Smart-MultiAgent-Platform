import { describe, expect, it, vi } from 'vitest'
import { ref, nextTick } from 'vue'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import ResearchExportButton from '../components/ResearchExportButton.vue'

vi.mock('@shared/composables', () => ({
  useToast: () => ({
    toast: vi.fn(),
  }),
}))

vi.mock('../api/researchExport', () => ({
  createResearchExport: vi.fn().mockResolvedValue({
    job_id: 'j1',
    status: 'queued',
  }),
  getResearchExportStatus: vi.fn().mockResolvedValue({
    job_id: 'j1',
    workspace_id: 'w1',
    status: 'ready',
    url: 'https://example.com/download',
    error: null,
  }),
}))

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
})
