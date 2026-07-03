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

const absolute = computed(() => formatDateTime(props.value))
const relative = computed(() => formatRelativeTime(props.value, now.value, locale.value))

// datetime is genuinely conditional (only when the value parses); omit the
// attr entirely rather than passing an explicit `undefined` value, which
// exactOptionalPropertyTypes forbids.
const datetimeAttrs = computed(() => {
  const iso = parsed.value?.toISOString()
  return iso !== undefined ? { datetime: iso } : {}
})
</script>

<template>
  <time
    v-bind="datetimeAttrs"
    :title="absolute"
  >{{ relative }}</time>
</template>
