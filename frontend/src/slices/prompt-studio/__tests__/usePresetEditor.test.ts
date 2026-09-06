import { mount } from '@vue/test-utils'
import { QueryClient, VueQueryPlugin } from '@tanstack/vue-query'
import { http, HttpResponse } from 'msw'
import { defineComponent, h, ref } from 'vue'
import { beforeAll, beforeEach, describe, expect, it } from 'vitest'

import { i18n } from '@shared/i18n'

import { server } from '../../../../tests/mocks/server'
import { usePresetEditor } from '../composables/usePresetEditor'
import { installMessages } from './kit'

const preset = {
  id: 'cfg_created',
  scope: 'platform',
  name: 'Newly Created',
  description: 'd',
  enabled: false,
  persona_prompt: 'persona text',
  system_prompt: '',
  key_id: null,
  key: null,
  key_revoked: false,
  model_id: null,
  daily_request_limit_per_user: 50,
  hide_platform_templates: false,
  version: 1,
  files: [],
}

function mountEditor(idRef: ReturnType<typeof ref<string | null>>) {
  let result!: ReturnType<typeof usePresetEditor>
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } })
  const Harness = defineComponent({
    setup() {
      result = usePresetEditor(() => idRef.value)
      return () => h('div')
    },
  })
  const wrapper = mount(Harness, { global: { plugins: [i18n, [VueQueryPlugin, { queryClient: qc }]] } })
  return { wrapper, get editor() { return result } }
}

describe('usePresetEditor reactivity across a create -> edit navigation', () => {
  beforeAll(installMessages)

  beforeEach(() => {
    server.use(
      http.get('/api/admin/prompt-assistant/presets', () => HttpResponse.json([preset])),
      http.get('/api/keys', () => HttpResponse.json([])),
      http.get('/api/model-catalog', () => HttpResponse.json({ chat: [] })),
    )
  })

  it('picks up the new preset once the id changes without remounting (AC-9 create flow)', async () => {
    const idRef = ref<string | null>(null)
    const { editor } = mountEditor(idRef)
    await new Promise((r) => setTimeout(r, 30))

    expect(editor.isNew.value).toBe(true)
    expect(editor.preset.value).toBeNull()

    // Simulate the router.replace() a successful create triggers, without
    // unmounting the component (Vue Router reuses the instance for
    // admin.promptPresetNew -> admin.promptPresetEdit, same component).
    idRef.value = 'cfg_created'
    await new Promise((r) => setTimeout(r, 30))

    expect(editor.isNew.value).toBe(false)
    expect(editor.preset.value?.id).toBe('cfg_created')
    expect(editor.form.name).toBe('Newly Created')
    expect(editor.form.persona_prompt).toBe('persona text')
  })
})
