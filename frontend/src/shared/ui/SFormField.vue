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
  const el = controlRef.value?.querySelector('input, select, textarea') as HTMLElement | null
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
  font-size: 0.875rem;
  font-weight: 500;
  color: var(--color-fg);
}
.form-field__error {
  font-size: 0.75rem;
  color: var(--color-danger);
  margin: 0;
}
.form-field__help {
  font-size: 0.75rem;
  color: var(--color-muted);
  margin: 0;
}
.form-field--error .form-field__control :deep(input),
.form-field--error .form-field__control :deep(select),
.form-field--error .form-field__control :deep(textarea) {
  border-color: var(--color-danger);
}
</style>
