<script setup lang="ts">
defineProps<{
  label: string
  name: string
  error?: string
  help?: string
  required?: boolean
}>()
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
    <div class="form-field__control">
      <slot :id="name" />
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
