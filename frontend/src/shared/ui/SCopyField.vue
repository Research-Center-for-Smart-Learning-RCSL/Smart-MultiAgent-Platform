<script setup lang="ts">
// A read-only value the user is meant to hand somewhere else: an invite accept
// link, an activation link, a guest URL.
//
// The value is rendered in a real <input> rather than as text so the browser's
// own select-all and keyboard copy still work when the Clipboard API is refused
// (a non-secure origin, a denied permission) — the button is the convenience,
// not the only route.

import { useI18n } from 'vue-i18n'
import { CheckIcon, ClipboardDocumentIcon } from '@heroicons/vue/24/outline'
import SButton from './SButton.vue'
import SFormField from './SFormField.vue'
import SInput from './SInput.vue'
import { useClipboard, useToast } from '@shared/composables'

const props = defineProps<{
  label: string
  /** Doubles as the input's id, so it must be unique on the page. */
  name: string
  value: string
  help?: string
}>()

const { t } = useI18n()
const toast = useToast()
const { copied, copy } = useClipboard()

async function onCopy(): Promise<void> {
  if (!(await copy(props.value))) toast.error(t('common.copyFailed'))
}
</script>

<template>
  <SFormField
    :label="label"
    :name="name"
    :help="help ?? ''"
    class="s-copy-field"
  >
    <div class="s-copy-field__row">
      <SInput
        :model-value="value"
        readonly
        class="s-copy-field__input"
      />
      <SButton
        type="button"
        variant="secondary"
        class="s-copy-field__button"
        :aria-label="`${label}: ${t('common.copy')}`"
        @click="onCopy"
      >
        <template #icon-left>
          <CheckIcon
            v-if="copied"
            class="w-4 h-4"
          />
          <ClipboardDocumentIcon
            v-else
            class="w-4 h-4"
          />
        </template>
        {{ copied ? t('common.copied') : t('common.copy') }}
      </SButton>
    </div>
  </SFormField>
</template>

<style scoped>
.s-copy-field__row {
  display: flex;
  gap: var(--space-2);
  align-items: center;
}

.s-copy-field__input {
  flex: 1;
  min-width: 0;
}
</style>
