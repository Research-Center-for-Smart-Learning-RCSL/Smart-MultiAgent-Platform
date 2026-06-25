<script setup lang="ts">
// Relative timestamp with an absolute tooltip (§2.2 Date column, §11.3). The
// label re-renders on the shared `useNow` tick so "2 minutes ago" never freezes
// while the page stays open, and the `title`/`datetime` carry the exact instant.
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useNow } from '@shared/composables'
import { formatDateTime, formatRelativeTime } from '@shared/utils/datetime'

const props = defineProps<{
  value: string | number | Date
}>()

const { locale } = useI18n()
const now = useNow()

const parsed = computed(() => {
  const d = new Date(props.value)
  return Number.isNaN(d.getTime()) ? null : d
})

const isoDatetime = computed(() => parsed.value?.toISOString())
const absolute = computed(() => formatDateTime(props.value))
const relative = computed(() => formatRelativeTime(props.value, now.value, locale.value))
</script>

<template>
  <time
    :datetime="isoDatetime"
    :title="absolute"
  >{{ relative }}</time>
</template>
