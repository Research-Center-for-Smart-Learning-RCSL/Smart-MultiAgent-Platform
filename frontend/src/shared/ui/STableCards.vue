<script setup lang="ts" generic="T extends Record<string, unknown> = Record<string, unknown>">
import { computed, useSlots } from 'vue'
import SCheckbox from './SCheckbox.vue'
import SSkeleton from './SSkeleton.vue'
import SEmptyState from './SEmptyState.vue'

const props = withDefaults(
  defineProps<{
    // Structurally compatible with STable's Column, declared rather than
    // imported so the mobile branch does not depend on the table's type. Only
    // these three members are read here; `cellType` is widened to `string`
    // because narrowing it would be a second copy of ColumnCellType, and every
    // call site is already checked against the real union by STable itself.
    columns: { key: string; label: string; cellType?: string }[]
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

/** Mirrors STable's own rule so a column's figures align in both branches. */
function isFigureColumn(col: { cellType?: string }): boolean {
  return col.cellType === 'number' || col.cellType === 'date'
}

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

// row[props.rowKey] indexes the generic T with a non-literal string key, which
// TypeScript cannot resolve through T's Record<string, unknown> constraint
// (the constraint guarantees a value exists, but not a type narrower than
// unknown). By convention rowKey names a field holding a unique row id
// (string or number), which is what Vue's :key requires.
function rowIdentity(row: T, index: number): PropertyKey {
  const value = row[props.rowKey] as unknown
  return (value as PropertyKey | undefined) ?? index
}

// emptyTitle/emptyDescription are genuinely optional (no default); omit the
// attrs entirely rather than passing an explicit `undefined` value, which
// exactOptionalPropertyTypes forbids.
const emptyStateAttrs = computed(() => ({
  ...(props.emptyTitle !== undefined && { title: props.emptyTitle }),
  ...(props.emptyDescription !== undefined && { text: props.emptyDescription }),
}))
</script>

<template>
  <div
    class="s-cards"
    :aria-busy="props.loading"
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
    <template v-else-if="props.data.length === 0">
      <slot name="empty">
        <SEmptyState v-bind="emptyStateAttrs" />
      </slot>
    </template>

    <!-- Data cards. Enter/Space activate the row only when focus is on the card
         itself (.self), so activating a nested control does not also fire it. -->
    <template v-else>
      <div
        v-for="(row, index) in props.data"
        :key="rowIdentity(row, index)"
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
                <dd
                  class="s-cards__value"
                  :class="{ 's-cards__value--figures': isFigureColumn(col) }"
                >
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
  gap: var(--space-2);
}

.s-cards__card {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: var(--space-3);
  padding: var(--space-3);
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  cursor: pointer;
  text-align: left;
  width: 100%;
}

.s-cards__card--static {
  flex-direction: column;
  gap: var(--space-2);
  cursor: default;
}

.s-cards__card--selected {
  background: var(--color-info-tint);
  border-color: var(--color-accent);
}

.s-cards__main {
  display: flex;
  align-items: flex-start;
  gap: var(--space-2);
  min-width: 0;
  flex: 1;
}

.s-cards__fields {
  display: flex;
  flex-direction: column;
  gap: var(--space-1-5);
  margin: 0;
  min-width: 0;
  flex: 1;
}

.s-cards__field {
  display: flex;
  flex-direction: column;
  gap: var(--space-0-5);
  min-width: 0;
}

.s-cards__label {
  font-size: var(--font-size-xs);
  font-weight: var(--weight-semibold);
  /* Sentence case, matching STable's header: the labels are the same $t()
     strings, and uppercasing one branch but not the other made the same table
     read differently on either side of the breakpoint. */
  letter-spacing: var(--tracking-tight);
  color: var(--color-muted);
}

.s-cards__value {
  margin: 0;
  font-size: var(--font-size-sm);
  color: var(--color-fg);
  min-width: 0;
  overflow-wrap: anywhere;
}

.s-cards__value--figures {
  font-variant-numeric: tabular-nums;
}

.s-cards__actions {
  display: flex;
  align-items: center;
  gap: var(--space-1);
  flex-shrink: 0;
}
</style>
