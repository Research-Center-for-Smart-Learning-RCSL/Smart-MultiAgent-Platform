<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { ChevronLeftIcon, ChevronRightIcon } from '@heroicons/vue/20/solid'

const props = defineProps<{
  page: number
  totalPages: number
  totalItems: number
  pageSize: number
}>()

const emit = defineEmits<{
  'update:page': [value: number]
}>()

const { t } = useI18n()

const rangeStart = computed(() =>
  props.totalItems === 0 ? 0 : (props.page - 1) * props.pageSize + 1,
)
const rangeEnd = computed(() =>
  Math.min(props.page * props.pageSize, props.totalItems),
)

const hasPrev = computed(() => props.page > 1)
const hasNext = computed(() => props.page < props.totalPages)

const visiblePages = computed(() => {
  const total = props.totalPages
  const current = props.page

  if (total <= 7) {
    return Array.from({ length: total }, (_, i) => i + 1)
  }

  const pages: (number | 'ellipsis-left' | 'ellipsis-right')[] = []

  pages.push(1)

  const leftNeighbor = current - 1
  const rightNeighbor = current + 1

  if (leftNeighbor > 2) {
    pages.push('ellipsis-left')
  }

  for (let p = Math.max(2, leftNeighbor); p <= Math.min(total - 1, rightNeighbor); p++) {
    pages.push(p)
  }

  if (rightNeighbor < total - 1) {
    pages.push('ellipsis-right')
  }

  if (total > 1) {
    pages.push(total)
  }

  return pages
})

function goTo(p: number) {
  if (p >= 1 && p <= props.totalPages && p !== props.page) {
    emit('update:page', p)
  }
}
</script>

<template>
  <div class="s-pagination">
    <span class="s-pagination__info">
      {{ t('app.pagination.showing', { start: rangeStart, end: rangeEnd, total: totalItems }) }}
    </span>
    <nav
      class="s-pagination__nav"
      :aria-label="t('app.pagination.label')"
    >
      <button
        class="s-pagination__btn"
        :class="{ 's-pagination__btn--disabled': !hasPrev }"
        type="button"
        :disabled="!hasPrev"
        :aria-label="t('app.pagination.prev')"
        @click="goTo(page - 1)"
      >
        <ChevronLeftIcon class="s-pagination__chevron" />
      </button>
      <template
        v-for="(item, index) in visiblePages"
        :key="index"
      >
        <span
          v-if="typeof item === 'string'"
          class="s-pagination__ellipsis"
        >
          ...
        </span>
        <button
          v-else
          class="s-pagination__btn"
          :class="{ 's-pagination__btn--active': item === page }"
          type="button"
          :aria-label="t('app.pagination.page', { page: item })"
          :aria-current="item === page ? 'page' : undefined"
          @click="goTo(item)"
        >
          {{ item }}
        </button>
      </template>
      <button
        class="s-pagination__btn"
        :class="{ 's-pagination__btn--disabled': !hasNext }"
        type="button"
        :disabled="!hasNext"
        :aria-label="t('app.pagination.next')"
        @click="goTo(page + 1)"
      >
        <ChevronRightIcon class="s-pagination__chevron" />
      </button>
    </nav>
  </div>
</template>

<style scoped>
.s-pagination {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.s-pagination__info {
  font-size: 14px;
  color: var(--color-muted);
}

.s-pagination__nav {
  display: flex;
  align-items: center;
  gap: 4px;
}

.s-pagination__btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  height: 32px;
  min-width: 32px;
  padding: 0 6px;
  background: var(--color-bg);
  color: var(--color-fg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  font-size: 0.875rem;
  font-weight: 500;
  cursor: pointer;
  transition:
    background var(--transition-fast),
    color var(--transition-fast);
}

.s-pagination__btn:hover:not(.s-pagination__btn--disabled):not(.s-pagination__btn--active) {
  background: var(--color-surface);
}

.s-pagination__btn:focus-visible {
  outline: none;
  box-shadow: var(--focus-ring);
}

.s-pagination__btn--active {
  background: var(--color-accent);
  color: #fff;
  border-color: var(--color-accent);
}

.s-pagination__btn--disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.s-pagination__chevron {
  width: 16px;
  height: 16px;
}

.s-pagination__ellipsis {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  font-size: 0.875rem;
  color: var(--color-muted);
  user-select: none;
}
</style>
