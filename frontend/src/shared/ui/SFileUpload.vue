<script setup lang="ts">
import { ref, computed } from 'vue'
import { ArrowUpTrayIcon } from '@heroicons/vue/24/outline'
import { XMarkIcon } from '@heroicons/vue/20/solid'

const props = withDefaults(defineProps<{
  accept?: string
  maxSize?: number
  multiple?: boolean
  disabled?: boolean
}>(), {
  accept: undefined,
  maxSize: undefined,
  multiple: false,
  disabled: false,
})

const emit = defineEmits<{
  files: [files: File[]]
  error: [message: string]
}>()

const fileInputRef = ref<HTMLInputElement | null>(null)
const selectedFiles = ref<File[]>([])
const isDragOver = ref(false)

const hasFiles = computed(() => selectedFiles.value.length > 0)

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function validateAndEmit(files: File[]) {
  if (props.maxSize) {
    const oversized = files.find(f => f.size > props.maxSize!)
    if (oversized) {
      emit('error', `File "${oversized.name}" exceeds maximum size of ${formatSize(props.maxSize)}`)
      return
    }
  }
  selectedFiles.value = props.multiple
    ? [...selectedFiles.value, ...files]
    : [...files]
  emit('files', [...selectedFiles.value])
}

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (!input.files?.length) return
  validateAndEmit(Array.from(input.files))
  input.value = ''
}

function onClick() {
  if (!props.disabled) {
    fileInputRef.value?.click()
  }
}

function onDragEnter(e: DragEvent) {
  e.preventDefault()
  if (!props.disabled) isDragOver.value = true
}

function onDragOver(e: DragEvent) {
  e.preventDefault()
  if (!props.disabled) isDragOver.value = true
}

function onDragLeave() {
  isDragOver.value = false
}

function onDrop(e: DragEvent) {
  e.preventDefault()
  isDragOver.value = false
  if (props.disabled || !e.dataTransfer?.files.length) return
  validateAndEmit(Array.from(e.dataTransfer.files))
}

function removeFile(index: number) {
  selectedFiles.value.splice(index, 1)
  emit('files', [...selectedFiles.value])
}

function clear() {
  selectedFiles.value = []
}

defineExpose({ clear })
</script>

<template>
  <div class="file-upload">
    <div
      class="file-upload__dropzone"
      :class="{
        'file-upload__dropzone--drag-over': isDragOver,
        'file-upload__dropzone--disabled': disabled,
      }"
      role="button"
      tabindex="0"
      @click="onClick"
      @keydown.enter="onClick"
      @keydown.space.prevent="onClick"
      @dragenter="onDragEnter"
      @dragover="onDragOver"
      @dragleave="onDragLeave"
      @drop="onDrop"
    >
      <slot>
        <ArrowUpTrayIcon
          class="file-upload__icon"
          aria-hidden="true"
        />
        <!-- TODO: replace with $t('shared.fileUpload.dropzone') when i18n key is added -->
        <span class="file-upload__text">Drop files here or click to browse</span>
      </slot>
    </div>
    <input
      ref="fileInputRef"
      type="file"
      class="file-upload__input"
      :accept="accept"
      :multiple="multiple"
      :disabled="disabled"
      aria-label="File upload"
      @change="onFileChange"
    >
    <ul
      v-if="hasFiles"
      class="file-upload__list"
    >
      <li
        v-for="(file, index) in selectedFiles"
        :key="`${file.name}-${file.size}-${index}`"
        class="file-upload__item"
      >
        <span class="file-upload__file-name">{{ file.name }}</span>
        <span class="file-upload__file-size">{{ formatSize(file.size) }}</span>
        <button
          type="button"
          class="file-upload__remove"
          aria-label="Remove file"
          @click.stop="removeFile(index)"
        >
          <XMarkIcon
            class="file-upload__remove-icon"
            aria-hidden="true"
          />
        </button>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.file-upload__input {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}

.file-upload__dropzone {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 0.5rem;
  min-height: 120px;
  padding: 1.5rem;
  border: 2px dashed var(--color-border);
  border-radius: var(--radius-lg);
  background: var(--color-bg);
  cursor: pointer;
  transition: border-color var(--transition-fast), background var(--transition-fast);
}

.file-upload__dropzone:hover {
  border-color: var(--color-accent);
}

.file-upload__dropzone--drag-over {
  border-color: var(--color-accent);
  background: var(--color-sidebar-hover);
}

.file-upload__dropzone--disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.file-upload__dropzone--disabled:hover {
  border-color: var(--color-border);
}

.file-upload__icon {
  width: 48px;
  height: 48px;
  color: var(--color-muted);
}

.file-upload__text {
  font-size: 0.875rem;
  color: var(--color-muted);
}

.file-upload__list {
  list-style: none;
  margin: 0.5rem 0 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.file-upload__item {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.375rem 0.5rem;
  border-radius: var(--radius-sm);
  background: var(--color-surface);
  font-size: 0.8125rem;
}

.file-upload__file-name {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--color-fg);
}

.file-upload__file-size {
  flex-shrink: 0;
  color: var(--color-muted);
  font-size: 0.75rem;
}

.file-upload__remove {
  display: flex;
  align-items: center;
  justify-content: center;
  flex-shrink: 0;
  width: 24px;
  height: 24px;
  padding: 0;
  border: none;
  border-radius: var(--radius-sm);
  background: transparent;
  color: var(--color-muted);
  cursor: pointer;
  transition: color var(--transition-fast), background var(--transition-fast);
}

.file-upload__remove:hover {
  color: var(--color-danger);
  background: var(--color-bg);
}

.file-upload__remove-icon {
  width: 14px;
  height: 14px;
}
</style>
