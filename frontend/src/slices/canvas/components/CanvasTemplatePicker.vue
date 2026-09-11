<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { useQuery, useQueryClient, useMutation } from '@tanstack/vue-query'
import { DocumentPlusIcon } from '@heroicons/vue/24/outline'
import { canvasKeys } from '../queries'
import * as canvasApi from '../api'
import type { CanvasTemplate, CanvasTemplateDetail } from '../types'

const { t } = useI18n()

const props = defineProps<{
  chatroomId: string
  projectId: string
}>()

const emit = defineEmits<{
  dismissed: []
  applied: []
}>()

const queryClient = useQueryClient()

const templatesQuery = useQuery({
  queryKey: computed(() => canvasKeys.templates(props.projectId)),
  queryFn: () =>
    canvasApi.listTemplates({
      project_id: props.projectId || undefined,
    }),
  enabled: computed(() => !!props.chatroomId),
})

const templates = computed<CanvasTemplate[]>(() => templatesQuery.data.value ?? [])

const templateDetails = ref<Record<string, CanvasTemplateDetail>>({})

watch(templates, async (list) => {
  for (const tmpl of list) {
    if (!templateDetails.value[tmpl.id]) {
      try {
        templateDetails.value[tmpl.id] = await canvasApi.getTemplate(tmpl.id)
      } catch {
        // preview unavailable
      }
    }
  }
}, { immediate: true })

const applyMut = useMutation({
  mutationFn: (templateId: string) =>
    canvasApi.applyTemplate(props.chatroomId, templateId),
  onSuccess: () => {
    queryClient.invalidateQueries({ queryKey: canvasKeys.objects(props.chatroomId) })
    emit('applied')
  },
})

function selectTemplate(template: CanvasTemplate) {
  applyMut.mutate(template.id)
}

interface PreviewRect {
  left: string
  top: string
  width: string
  height: string
  background: string
  borderRadius: string
  border: string
}

const PREVIEW_COLORS: Record<string, string> = {
  note: '#fef3c7',
  text: '#dbeafe',
  shape: '#e5e7eb',
  connector: '#d1d5db',
  drawing: '#9ca3af',
  image: '#dcfce7',
}

function previewRects(templateId: string): PreviewRect[] {
  const detail = templateDetails.value[templateId]
  if (!detail) return []
  const objects = detail.template_data.objects
  if (!objects.length) return []

  const xs = objects.map((o) => o.position_x)
  const ys = objects.map((o) => o.position_y)
  const rights = objects.map((o) => o.position_x + o.width)
  const bottoms = objects.map((o) => o.position_y + o.height)
  const minX = Math.min(...xs)
  const minY = Math.min(...ys)
  const totalW = Math.max(...rights) - minX
  const totalH = Math.max(...bottoms) - minY
  const scale = Math.min(160 / (totalW || 1), 80 / (totalH || 1), 1)

  return objects.map((obj) => ({
    left: `${(obj.position_x - minX) * scale}px`,
    top: `${(obj.position_y - minY) * scale}px`,
    width: `${obj.width * scale}px`,
    height: `${obj.height * scale}px`,
    background:
      (obj.style as Record<string, string> | undefined)?.backgroundColor ||
      PREVIEW_COLORS[obj.kind ?? 'shape'] ||
      '#e5e7eb',
    borderRadius: '2px',
    border: obj.kind === 'connector' ? '1px solid #d1d5db' : 'none',
  }))
}
</script>

<template>
  <div class="template-picker">
    <div class="template-picker__header">
      <DocumentPlusIcon class="template-picker__header-icon" />
      <h3 class="template-picker__title">
        {{ t('canvas.selectTemplate') }}
      </h3>
    </div>

    <div class="template-picker__grid">
      <button
        v-for="tmpl in templates"
        :key="tmpl.id"
        class="template-card"
        :disabled="applyMut.isPending.value"
        @click="selectTemplate(tmpl)"
      >
        <div class="template-card__preview">
          <div class="template-card__preview-shapes">
            <div
              v-for="(rect, idx) in previewRects(tmpl.id)"
              :key="idx"
              class="template-card__rect"
              :style="{
                left: rect.left,
                top: rect.top,
                width: rect.width,
                height: rect.height,
                background: rect.background,
                borderRadius: rect.borderRadius,
                border: rect.border,
              }"
            />
          </div>
        </div>
        <div class="template-card__info">
          <span class="template-card__name">{{ tmpl.name }}</span>
          <span
            v-if="tmpl.description"
            class="template-card__desc"
          >{{ tmpl.description }}</span>
        </div>
      </button>
    </div>

    <button
      class="template-picker__blank"
      @click="emit('dismissed')"
    >
      {{ t('canvas.startBlank') }}
    </button>
  </div>
</template>

<style scoped>
.template-picker {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: var(--space-4);
  padding: var(--space-6) var(--space-4);
  max-width: 640px;
  margin: 0 auto;
}

.template-picker__header {
  display: flex;
  align-items: center;
  gap: var(--space-2);
}

.template-picker__header-icon {
  width: 24px;
  height: 24px;
  color: var(--color-muted);
}

.template-picker__title {
  font-size: var(--font-size-lg);
  font-weight: var(--weight-semibold);
  color: var(--color-fg);
  margin: 0;
}

.template-picker__grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: var(--space-3);
  width: 100%;
}

.template-card {
  display: flex;
  flex-direction: column;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: var(--color-surface);
  cursor: pointer;
  text-align: left;
  padding: 0;
  transition: border-color 0.15s, box-shadow 0.15s;
}

.template-card:hover {
  border-color: var(--color-accent);
  box-shadow: 0 0 0 1px var(--color-accent);
}

.template-card:disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.template-card__preview {
  height: 100px;
  overflow: hidden;
  border-bottom: 1px solid var(--color-border);
  border-radius: var(--radius-md) var(--radius-md) 0 0;
  background: var(--color-canvas);
  padding: var(--space-1);
  display: flex;
  align-items: center;
  justify-content: center;
}

.template-card__preview-shapes {
  position: relative;
  width: 100%;
  height: 100%;
}

.template-card__rect {
  position: absolute;
  opacity: 0.8;
}

.template-card__info {
  display: flex;
  flex-direction: column;
  gap: var(--space-0-5);
  padding: var(--space-2);
}

.template-card__name {
  font-size: var(--font-size-sm);
  font-weight: var(--weight-medium);
  color: var(--color-fg);
}

.template-card__desc {
  font-size: var(--font-size-xs);
  color: var(--color-muted);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.template-picker__blank {
  padding: var(--space-2) var(--space-4);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  background: transparent;
  color: var(--color-muted);
  cursor: pointer;
  font-size: var(--font-size-sm);
  transition: background 0.15s, color 0.15s;
}

.template-picker__blank:hover {
  background: var(--color-surface-hover);
  color: var(--color-fg);
}
</style>
