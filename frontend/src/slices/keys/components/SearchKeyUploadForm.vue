<script setup lang="ts">
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { SModal, SFormField, SSelect, SInput, SButton } from '@shared/ui'
import type { SearchProvider } from '../api/search-keys'

const props = defineProps<{ open: boolean }>()
const emit = defineEmits<{
  (e: 'close'): void
  (e: 'submit', payload: { provider: SearchProvider; secret: string; config: Record<string, unknown> }): void
}>()

const { t } = useI18n()

const provider = ref<SearchProvider>('brave')
const secret = ref('')
const cx = ref('')
const depth = ref<'basic' | 'advanced'>('basic')
const uploadError = ref('')

const providerOptions = computed(() => [
  { value: 'brave', label: t('keys.search.brave') },
  { value: 'serper', label: t('keys.search.serper') },
  { value: 'tavily', label: t('keys.search.tavily') },
  { value: 'google_cse', label: t('keys.search.googleCse') },
])

const depthOptions = computed(() => [
  { value: 'basic', label: t('keys.search.depthBasic') },
  { value: 'advanced', label: t('keys.search.depthAdvanced') },
])

function reset() {
  secret.value = ''
  cx.value = ''
  depth.value = 'basic'
  uploadError.value = ''
}

function onClose() {
  reset()
  emit('close')
}

function onSubmit() {
  if (!secret.value.trim()) return
  const config: Record<string, unknown> = {}
  if (provider.value === 'google_cse') config.cx = cx.value.trim()
  if (provider.value === 'tavily') config.search_depth = depth.value
  emit('submit', { provider: provider.value, secret: secret.value, config })
}

function setError(msg: string) {
  uploadError.value = msg
}

defineExpose({ reset, setError })
</script>

<template>
  <SModal
    :open="props.open"
    :title="t('keys.search.addTitle')"
    size="md"
    @close="onClose"
  >
    <form
      id="add-search-key-form"
      @submit.prevent="onSubmit"
    >
      <div class="flex flex-col gap-4">
        <SFormField
          :label="t('keys.search.providerLabel')"
          name="provider"
          required
        >
          <SSelect
            v-model="provider"
            :options="providerOptions"
            data-testid="search-provider"
          />
        </SFormField>

        <SFormField
          :label="t('keys.search.secret')"
          name="secret"
          :error="uploadError"
          required
        >
          <SInput
            v-model="secret"
            type="password"
            autocomplete="off"
            :error="!!uploadError"
            data-testid="search-secret"
          />
        </SFormField>

        <SFormField
          v-if="provider === 'google_cse'"
          :label="t('keys.search.cx')"
          name="cx"
          required
        >
          <SInput
            v-model="cx"
            data-testid="search-cx"
          />
        </SFormField>

        <SFormField
          v-if="provider === 'tavily'"
          :label="t('keys.search.searchDepth')"
          name="depth"
        >
          <SSelect
            v-model="depth"
            :options="depthOptions"
          />
        </SFormField>
      </div>
    </form>

    <template #footer>
      <div class="flex justify-end gap-3">
        <SButton
          variant="secondary"
          @click="onClose"
        >
          {{ t('app.cancel') }}
        </SButton>
        <SButton
          variant="primary"
          type="submit"
          form="add-search-key-form"
          data-testid="search-upload"
        >
          {{ t('keys.search.upload') }}
        </SButton>
      </div>
    </template>
  </SModal>
</template>
