<script lang="ts">
// Module-scoped memo so the same artifact is presigned once across every bubble
// that renders it, not on each re-render. Presigned URLs are short-lived (~15
// min); on an image load error we drop the entry and refetch once.
import { getAttachment } from '../api'

const urlCache = new Map<string, Promise<string | null>>()

function fetchUrl(id: string): Promise<string | null> {
  let pending = urlCache.get(id)
  if (!pending) {
    pending = getAttachment(id)
      .then((d) => d.url)
      .catch(() => null)
    urlCache.set(id, pending)
  }
  return pending
}

function invalidate(id: string): void {
  urlCache.delete(id)
}
</script>

<script setup lang="ts">
import { ref, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { PaperClipIcon } from '@heroicons/vue/24/outline'

const props = defineProps<{
  attachmentId: string
  filename: string
}>()

const emit = defineEmits<{ download: [] }>()

const { t } = useI18n()
const url = ref<string | null>(null)
const failed = ref(false)
const retried = ref(false)

async function load(): Promise<void> {
  failed.value = false
  const resolved = await fetchUrl(props.attachmentId)
  if (resolved) {
    url.value = resolved
  } else {
    failed.value = true
  }
}

function onError(): void {
  // A presigned URL likely expired — drop the memo and try once more.
  if (retried.value) {
    failed.value = true
    url.value = null
    return
  }
  retried.value = true
  invalidate(props.attachmentId)
  url.value = null
  void load()
}

onMounted(load)
</script>

<template>
  <img
    v-if="url"
    :src="url"
    :alt="filename"
    class="attachment-image"
    loading="lazy"
    @error="onError"
  >
  <button
    v-else-if="failed"
    type="button"
    class="attachment-image__fallback"
    @click="emit('download')"
  >
    <PaperClipIcon class="attachment-image__icon" />
    {{ filename }}
  </button>
  <span
    v-else
    class="attachment-image__loading"
  >{{ t('conversation.chatroom.attachmentLoading') }}</span>
</template>

<style scoped>
.attachment-image {
  display: block;
  max-width: min(420px, 100%);
  max-height: 360px;
  width: auto;
  height: auto;
  margin-top: 8px;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  object-fit: contain;
  background: var(--color-surface);
}

.attachment-image__fallback {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  background: none;
  border: none;
  padding: 0;
  margin-top: 8px;
  font-size: 13px;
  color: var(--color-accent);
  cursor: pointer;
}

.attachment-image__icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.attachment-image__loading {
  display: inline-block;
  margin-top: 8px;
  font-size: 12px;
  font-style: italic;
  color: var(--color-muted);
}
</style>
