import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type * as VueQuery from '@tanstack/vue-query'

import { ApiError } from '@shared/errors'
import { i18n } from '@shared/i18n'

const mutations = vi.hoisted(() => ({
  patchTemplate: vi.fn(),
  saveConfig: vi.fn(),
}))
const sonner = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))

vi.mock('vue-sonner', () => ({ toast: sonner }))
vi.mock('@tanstack/vue-query', async () => {
  const { ref } = await import('vue')
  const actual = await vi.importActual<typeof VueQuery>('@tanstack/vue-query')
  return { ...actual, useQuery: () => ({ data: ref([]) }) }
})
vi.mock('@shared/composables', async () => {
  const { ref } = await import('vue')
  return {
    useConfirmDialog: () => ({ confirm: vi.fn() }),
    useModelCatalog: () => ({ data: ref({ chat: [] }) }),
    useToast: () => sonner,
  }
})
vi.mock('../queries', async () => {
  const { ref } = await import('vue')
  const idleMutation = () => ({ mutateAsync: vi.fn(), isPending: ref(false) })
  return {
    useTemplatesQuery: () => ({ data: ref([]) }),
    useCreateTemplateMutation: idleMutation,
    useDeleteTemplateMutation: idleMutation,
    usePatchTemplateMutation: () => ({ mutateAsync: mutations.patchTemplate, isPending: ref(false) }),
    useConfigQuery: () => ({ data: ref(undefined) }),
    useDeleteFileMutation: idleMutation,
    useUploadFileMutation: idleMutation,
    useSaveConfigMutation: () => ({ mutateAsync: mutations.saveConfig, isPending: ref(false) }),
  }
})

import { useConfigEditor } from '../composables/useConfigEditor'
import { useTemplateEditor } from '../composables/useTemplateEditor'

const scope = { kind: 'user' as const }

function conflict(): ApiError {
  return new ApiError({
    type: 'https://smap.local/problems/prompt-studio/version-mismatch',
    title: 'Version conflict',
    status: 412,
  })
}

describe('prompt-studio conflict severity', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mutations.patchTemplate.mockRejectedValue(conflict())
    mutations.saveConfig.mockRejectedValue(conflict())
  })

  it('uses warning feedback for both template and config conflicts', async () => {
    let template!: ReturnType<typeof useTemplateEditor>
    let config!: ReturnType<typeof useConfigEditor>
    const Harness = defineComponent({
      setup() {
        template = useTemplateEditor(scope)
        config = useConfigEditor(scope)
        return () => h('div')
      },
    })
    mount(Harness, { global: { plugins: [i18n] } })

    await template.onUpdate({ id: 'tpl_1', version: 1, name: 'T', description: '', body: 'Body' })
    await config.save()

    expect(sonner.warning).toHaveBeenNthCalledWith(
      1,
      'promptStudio.templates.conflict',
    )
    expect(sonner.warning).toHaveBeenNthCalledWith(
      2,
      'promptStudio.config.conflict',
    )
    expect(sonner.error).not.toHaveBeenCalled()
  })
})
