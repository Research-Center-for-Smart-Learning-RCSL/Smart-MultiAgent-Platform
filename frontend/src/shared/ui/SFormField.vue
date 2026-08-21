<script setup lang="ts">
import { ref, watch, onMounted, nextTick } from 'vue'

const props = defineProps<{
  label: string
  name: string
  error?: string
  help?: string
  required?: boolean
}>()

const controlRef = ref<HTMLElement | null>(null)

function syncAria() {
  // `[contenteditable="true"]` covers CodeMirror-backed fields (SCodeEditor
  // with language="json") — a contenteditable div, not a native form
  // control, so it needs its own match or this never re-fires once the
  // control swaps from a <textarea> to CodeMirror's content element.
  const el = controlRef.value?.querySelector(
    'input, select, textarea, [contenteditable="true"]',
  ) as HTMLElement | null
  if (!el) return
  // Associate the control with our <label for="name"> so clicking the label
  // focuses it and assistive tech / getByLabel can resolve the accessible name.
  // Respect an explicit id the control may already carry.
  if (!el.id) el.id = props.name
  const describedBy = props.error ? `${props.name}-error` : props.help ? `${props.name}-help` : null
  if (describedBy) el.setAttribute('aria-describedby', describedBy)
  else el.removeAttribute('aria-describedby')
  if (props.error) el.setAttribute('aria-invalid', 'true')
  else el.removeAttribute('aria-invalid')
}

onMounted(syncAria)
watch(() => [props.error, props.help], () => nextTick(syncAria))
</script>

<template>
  <div
    class="form-field"
    :class="{ 'form-field--error': !!error }"
  >
    <label
      :for="name"
      class="form-field__label"
    >
      {{ label }}
      <span
        v-if="required"
        aria-hidden="true"
      >*</span>
    </label>
    <div
      ref="controlRef"
      class="form-field__control"
    >
      <slot />
    </div>
    <p
      v-if="error"
      :id="`${name}-error`"
      class="form-field__error"
      role="alert"
    >
      {{ error }}
    </p>
    <p
      v-else-if="help"
      :id="`${name}-help`"
      class="form-field__help"
    >
      {{ help }}
    </p>
  </div>
</template>

<style scoped>
.form-field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin-bottom: 1rem;
}
.form-field__label {
  font-size: var(--font-size-sm);
  font-weight: var(--weight-medium);
  color: var(--color-fg);
}
.form-field__error {
  font-size: var(--font-size-xs);
  color: var(--color-danger);
  margin: 0;
}
.form-field__help {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
  margin: 0;
}
.form-field--error .form-field__control :deep(input),
.form-field--error .form-field__control :deep(select),
.form-field--error .form-field__control :deep(textarea),
.form-field--error .form-field__control :deep([contenteditable='true']) {
  border-color: var(--color-danger);
}
</style>
