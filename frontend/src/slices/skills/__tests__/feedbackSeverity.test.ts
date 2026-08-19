import { mount } from '@vue/test-utils'
import { defineComponent, h, ref } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { ApiError } from '@shared/errors'
import { i18n } from '@shared/i18n'

const mutations = vi.hoisted(() => ({
  patch: vi.fn(),
  restore: vi.fn(),
}))
const sonner = vi.hoisted(() => ({
  success: vi.fn(),
  error: vi.fn(),
  warning: vi.fn(),
  info: vi.fn(),
}))

vi.mock('vue-sonner', () => ({ toast: sonner }))
vi.mock('@shared/composables', async () => {
  return { useToast: () => sonner, useConfirmDialog: () => ({ confirm: vi.fn() }) }
})
vi.mock('../queries', async () => {
  const { ref } = await import('vue')
  return {
    useSkillQuery: () => ({
      data: ref({
        id: 'skill_1',
        version: 1,
        description: 'Description',
        body: 'Body',
        requires: [],
        allowed_tools: [],
        deleted_at: null,
      }),
    }),
    usePatchSkillMutation: () => ({ mutateAsync: mutations.patch, isPending: ref(false) }),
    useRestoreSkillMutation: () => ({ mutateAsync: mutations.restore, isPending: ref(false) }),
    useDeleteSkillMutation: () => ({ mutateAsync: vi.fn(), isPending: ref(false) }),
  }
})

import { useSkillEditor } from '../composables/useSkillEditor'

function problem(type: string): ApiError {
  return new ApiError({
    type: `https://smap.local/problems/${type}`,
    title: 'Version conflict',
    status: 412,
    current_version: 2,
  })
}

describe('skill conflict severity', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mutations.patch.mockRejectedValue(problem('skills/version-mismatch'))
    mutations.restore.mockRejectedValue(problem('skills/restore-conflict'))
  })

  it('uses warning feedback for save and restore conflicts', async () => {
    let editor!: ReturnType<typeof useSkillEditor>
    const Harness = defineComponent({
      setup() {
        editor = useSkillEditor({ kind: 'platform' }, ref('skill_1'))
        return () => h('div')
      },
    })
    mount(Harness, { global: { plugins: [i18n] } })

    await editor.save()
    await editor.restore()

    expect(sonner.warning).toHaveBeenNthCalledWith(1, 'skills.editor.conflict')
    expect(sonner.warning).toHaveBeenNthCalledWith(
      2,
      'skills.editor.restoreConflict',
    )
    expect(sonner.error).not.toHaveBeenCalled()
  })
})
