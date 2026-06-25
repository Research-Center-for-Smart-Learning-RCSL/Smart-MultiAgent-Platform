<script setup lang="ts" generic="T extends Record<string, unknown> = Record<string, unknown>">
import { computed, useSlots } from 'vue'
import SCheckbox from './SCheckbox.vue'
import SSkeleton from './SSkeleton.vue'
import SEmptyState from './SEmptyState.vue'

const props = withDefaults(
  defineProps<{
    // Structurally compatible with STable's Column (only key/label are used here).
    columns: { key: string; label: string }[]
    data?: T[]
    loading?: boolean
    emptyTitle?: string
    emptyDescription?: string
    selectable?: boolean
    selected?: unknown[]
    rowKey?: string
  }>(),
  {
    data: () => [],
    loading: false,
    emptyTitle: undefined,
    emptyDescription: undefined,
    selectable: false,
    selected: () => [],
    rowKey: 'id',
  },
)

// Selection mutation and row activation are owned by the parent STable; this
// component only renders and reports intent so the logic stays single-sourced.
const emit = defineEmits<{
  'row-click': [row: T]
  'toggle-select': [row: T]
}>()

const slots = useSlots()
const hasActionsSlot = computed(() => !!slots['actions'])

// Render a card field for any column that has a label OR a cell slot (so an
// empty-label column with real content — an in-column actions menu, an avatar —
// still appears; only purely structural columns like a divider are skipped).
const fieldColumns = computed(() =>
  props.columns.filter((c) => c.label !== '' || !!slots[`cell-${c.key}`]),
)

// O(1) selection lookup instead of scanning props.selected per binding per row.
const selectedSet = computed(() => new Set(props.selected))
function isRowSelected(row: T): boolean {
  return selectedSet.value.has(row[props.rowKey])
}

const skeletonRows = 5
</script>

<template>
  <div
    class="s-cards"
    :aria-busy="loading"
  >
    <!-- Loading skeleton cards -->
    <template v-if="loading">
      <div
        v-for="r in skeletonRows"
        :key="`card-skel-${r}`"
        class="s-cards__card s-cards__card--static"
      >
        <SSkeleton
          variant="text"
          width="60%"
        />
        <SSkeleton
          variant="text"
          width="40%"
        />
      </div>
    </template>

    <!-- Empty state -->
    <template v-else-if="data.length === 0">
      <slot name="empty">
        <SEmptyState
          :title="emptyTitle"
          :text="emptyDescription"
        />
      </slot>
    </template>

    <!-- Data cards. Enter/Space activate the row only when focus is on the card
         itself (.self), so activating a nested control does not also fire it. -->
    <template v-else>
      <div
        v-for="(row, index) in data"
        :key="row[rowKey] ?? index"
        class="s-cards__card"
        :class="{ 's-cards__card--selected': selectable && isRowSelected(row) }"
        role="button"
        tabindex="0"
        @click="emit('row-click', row)"
        @keydown.enter.self="emit('row-click', row)"
        @keydown.space.self.prevent="emit('row-click', row)"
      >
        <slot
          name="mobile-card"
          :row="row"
          :index="index"
          :selected="selectable && isRowSelected(row)"
        >
          <div class="s-cards__main">
            <SCheckbox
              v-if="selectable"
              :model-value="isRowSelected(row)"
              @click.stop
              @update:model-value="emit('toggle-select', row)"
            />
            <dl class="s-cards__fields">
              <div
                v-for="col in fieldColumns"
                :key="col.key"
                class="s-cards__field"
              >
                <dt
                  v-if="col.label"
                  class="s-cards__label"
                >
                  {{ col.label }}
                </dt>
                <dd class="s-cards__value">
                  <slot
                    :name="`cell-${col.key}`"
                    :row="row"
                    :value="row[col.key]"
                    :index="index"
                  >
                    {{ row[col.key] }}
                  </slot>
                </dd>
              </div>
            </dl>
          </div>
          <div
            v-if="hasActionsSlot"
            class="s-cards__actions"
            @click.stop
          >
            <slot
              name="actions"
              :row="row"
              :index="index"
            />
          </div>
        </slot>
      </div>
    </template>
  </div>
</template>

<style scoped>
.s-cards {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.s-cards__card {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 12px;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  text-align: left;
  width: 100%;
}

.s-cards__card--static {
  flex-direction: column;
  gap: 8px;
  cursor: default;
}

.s-cards__card--selected {
  background: var(--color-info-tint);
  border-color: var(--color-accent);
}

.s-cards__main {
  display: flex;
  align-items: flex-start;
  gap: 8px;
  min-width: 0;
  flex: 1;
}

.s-cards__fields {
  display: flex;
  flex-direction: column;
  gap: 6px;
  margin: 0;
  min-width: 0;
  flex: 1;
}

.s-cards__field {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.s-cards__label {
  font-size: 12px;
  font-weight: 600;
  text-transform: uppercase;
  color: var(--color-muted);
}

.s-cards__value {
  margin: 0;
  font-size: 14px;
  color: var(--color-fg);
  min-width: 0;
  overflow-wrap: anywhere;
}

.s-cards__actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}
</style>
