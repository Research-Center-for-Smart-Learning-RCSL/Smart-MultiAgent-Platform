<script setup lang="ts" generic="T extends Record<string, unknown> = Record<string, unknown>">
import { computed, useSlots } from 'vue'
import {
  ChevronUpDownIcon,
  ChevronUpIcon,
  ChevronDownIcon,
} from '@heroicons/vue/20/solid'
import { useBreakpoint, BP } from '@shared/composables'
import STableCards from './STableCards.vue'

export interface Column {
  key: string
  label: string
  sortable?: boolean
  width?: string
  align?: 'left' | 'center' | 'right'
  /** Hide this column when the viewport is narrower than the given breakpoint
      (only applies when responsiveMode === 'hide-columns'). */
  hideBelow?: 'sm' | 'md' | 'lg' | 'xl'
}

type RowData = Record<string, unknown>

const props = withDefaults(
  defineProps<{
    columns: Column[]
    data?: T[]
    loading?: boolean
    emptyTitle?: string
    emptyDescription?: string
    sortBy?: string
    sortOrder?: 'asc' | 'desc'
    selectable?: boolean
    selected?: unknown[]
    rowKey?: string
    stickyHeader?: boolean
    loadingLabel?: string
    responsiveMode?: 'hide-columns' | 'card-list'
  }>(),
  {
    data: () => [],
    loading: false,
    emptyTitle: undefined,
    emptyDescription: undefined,
    sortBy: undefined,
    sortOrder: 'asc',
    selectable: false,
    selected: () => [],
    rowKey: 'id',
    stickyHeader: false,
    loadingLabel: 'Loading',
    responsiveMode: 'card-list',
  },
)

const emit = defineEmits<{
  sort: [payload: { key: string; order: 'asc' | 'desc' }]
  'update:selected': [payload: unknown[]]
  'row-click': [row: T]
}>()

const slots = useSlots()
const { width, isMobile } = useBreakpoint()

const hasActionsSlot = computed(() => !!slots['actions'])

// Render a stacked card list (instead of a table) below md when enabled.
const isCardList = computed(
  () => props.responsiveMode === 'card-list' && isMobile.value,
)

// In hide-columns mode, drop columns whose hideBelow threshold isn't met.
const visibleColumns = computed(() =>
  props.responsiveMode === 'hide-columns'
    ? props.columns.filter((c) => !c.hideBelow || width.value >= BP[c.hideBelow])
    : props.columns,
)

// Columns passed to the mobile card list. Only hideBelow filtering happens here;
// STableCards itself decides which columns render a labeled field vs. an
// unlabeled cell (e.g. an in-column actions menu or avatar) so that empty-label
// columns with real cell content are NOT dropped.
const cardColumns = computed(() =>
  props.columns.filter((c) => !c.hideBelow || width.value >= BP[c.hideBelow]),
)

function ariaSort(col: Column): 'ascending' | 'descending' | 'none' | undefined {
  if (!col.sortable) return undefined
  if (props.sortBy !== col.key) return 'none'
  return props.sortOrder === 'asc' ? 'ascending' : 'descending'
}

const totalColumns = computed(() => {
  let count = visibleColumns.value.length
  if (props.selectable) count++
  if (hasActionsSlot.value) count++
  return count
})

const allSelected = computed(() => {
  if (props.data.length === 0) return false
  return props.data.every((row) => props.selected.includes(row[props.rowKey]))
})

const someSelected = computed(() => {
  if (allSelected.value) return false
  return props.data.some((row) => props.selected.includes(row[props.rowKey]))
})

function handleSort(col: Column) {
  if (!col.sortable) return
  let order: 'asc' | 'desc' = 'asc'
  if (props.sortBy === col.key) {
    order = props.sortOrder === 'asc' ? 'desc' : 'asc'
  }
  emit('sort', { key: col.key, order })
}

function toggleAll() {
  if (allSelected.value) {
    emit('update:selected', [])
  } else {
    emit(
      'update:selected',
      props.data.map((row) => row[props.rowKey]),
    )
  }
}

function toggleRow(row: RowData) {
  const key = row[props.rowKey]
  const idx = props.selected.indexOf(key)
  if (idx === -1) {
    emit('update:selected', [...props.selected, key])
  } else {
    const next = [...props.selected]
    next.splice(idx, 1)
    emit('update:selected', next)
  }
}

function isRowSelected(row: RowData): boolean {
  return props.selected.includes(row[props.rowKey])
}

function onRowClick(row: T) {
  emit('row-click', row)
}

const skeletonRows = 5
</script>

<template>
  <div class="s-table-wrap">
    <!-- Screen-reader loading announcement (skeleton rows are visual-only) -->
    <span
      v-if="loading"
      class="sr-only"
      role="status"
      aria-live="polite"
    >{{ loadingLabel }}</span>

    <!-- Bulk actions bar -->
    <div
      v-if="selectable && selected.length > 0"
      class="s-table-bulk"
    >
      <slot
        name="bulk-actions"
        :selected="selected"
        :count="selected.length"
      />
    </div>

    <table
      v-if="!isCardList"
      class="s-table"
      :aria-busy="loading"
    >
      <thead :class="{ 's-table__thead--sticky': stickyHeader }">
        <tr>
          <!-- Select-all checkbox -->
          <th
            v-if="selectable"
            class="s-table__th s-table__th--checkbox"
          >
            <label class="s-table__checkbox-label">
              <input
                type="checkbox"
                class="s-table__checkbox"
                :checked="allSelected"
                :indeterminate="someSelected"
                @change="toggleAll"
              >
            </label>
          </th>
          <!-- Column headers -->
          <th
            v-for="col in visibleColumns"
            :key="col.key"
            class="s-table__th"
            :class="{
              's-table__th--sortable': col.sortable,
              's-table__th--sorted': sortBy === col.key,
            }"
            :style="{
              width: col.width,
              textAlign: col.align || 'left',
            }"
            :aria-sort="ariaSort(col)"
            @click="handleSort(col)"
          >
            <span class="s-table__th-content">
              <span>{{ col.label }}</span>
              <template v-if="col.sortable">
                <ChevronUpIcon
                  v-if="sortBy === col.key && sortOrder === 'asc'"
                  class="s-table__sort-icon s-table__sort-icon--active"
                  aria-hidden="true"
                />
                <ChevronDownIcon
                  v-else-if="sortBy === col.key && sortOrder === 'desc'"
                  class="s-table__sort-icon s-table__sort-icon--active"
                  aria-hidden="true"
                />
                <ChevronUpDownIcon
                  v-else
                  class="s-table__sort-icon"
                  aria-hidden="true"
                />
              </template>
            </span>
          </th>
          <!-- Actions column header -->
          <th
            v-if="hasActionsSlot"
            class="s-table__th s-table__th--actions"
          >
            <span class="sr-only">Actions</span>
          </th>
        </tr>
      </thead>

      <tbody>
        <!-- Loading skeleton rows -->
        <template v-if="loading">
          <tr
            v-for="r in skeletonRows"
            :key="`skel-${r}`"
            class="s-table__row"
          >
            <td
              v-if="selectable"
              class="s-table__td s-table__td--checkbox"
            >
              <div
                class="s-table__skeleton"
                style="width: 18px; height: 18px;"
              />
            </td>
            <td
              v-for="col in visibleColumns"
              :key="`skel-${r}-${col.key}`"
              class="s-table__td"
              :style="{ textAlign: col.align || 'left' }"
            >
              <div
                class="s-table__skeleton"
                :style="{ width: col.width || '70%', height: '14px' }"
              />
            </td>
            <td
              v-if="hasActionsSlot"
              class="s-table__td s-table__td--actions"
            >
              <div
                class="s-table__skeleton"
                style="width: 60px; height: 14px;"
              />
            </td>
          </tr>
        </template>

        <!-- Empty state -->
        <template v-else-if="data.length === 0">
          <tr>
            <td
              :colspan="totalColumns"
              class="s-table__empty"
            >
              <slot name="empty">
                <div class="s-table__empty-inner">
                  <p
                    v-if="emptyTitle"
                    class="s-table__empty-title"
                  >
                    {{ emptyTitle }}
                  </p>
                  <p
                    v-if="emptyDescription"
                    class="s-table__empty-desc"
                  >
                    {{ emptyDescription }}
                  </p>
                </div>
              </slot>
            </td>
          </tr>
        </template>

        <!-- Data rows -->
        <template v-else>
          <tr
            v-for="(row, index) in data"
            :key="row[rowKey] ?? index"
            class="s-table__row s-table__row--clickable"
            :class="{ 's-table__row--selected': selectable && isRowSelected(row) }"
            @click="onRowClick(row)"
          >
            <!-- Row checkbox -->
            <td
              v-if="selectable"
              class="s-table__td s-table__td--checkbox"
              @click.stop
            >
              <label class="s-table__checkbox-label">
                <input
                  type="checkbox"
                  class="s-table__checkbox"
                  :checked="isRowSelected(row)"
                  @change="toggleRow(row)"
                >
              </label>
            </td>
            <!-- Data cells -->
            <td
              v-for="col in visibleColumns"
              :key="col.key"
              class="s-table__td"
              :style="{ textAlign: col.align || 'left' }"
            >
              <slot
                :name="`cell-${col.key}`"
                :row="row"
                :value="row[col.key]"
                :index="index"
              >
                {{ row[col.key] }}
              </slot>
            </td>
            <!-- Row actions -->
            <td
              v-if="hasActionsSlot"
              class="s-table__td s-table__td--actions"
              @click.stop
            >
              <slot
                name="actions"
                :row="row"
                :index="index"
              />
            </td>
          </tr>
        </template>
      </tbody>
    </table>

    <!-- Mobile card list (responsiveMode === 'card-list', below md).
         Slots are forwarded so cell-*/actions/mobile-card/empty work in cards. -->
    <STableCards
      v-else
      :columns="cardColumns"
      :data="data"
      :loading="loading"
      :selectable="selectable"
      :selected="selected"
      :row-key="rowKey"
      :empty-title="emptyTitle"
      :empty-description="emptyDescription"
      @row-click="onRowClick"
      @toggle-select="toggleRow"
    >
      <template
        v-for="(_, name) in $slots"
        #[name]="slotProps"
      >
        <slot
          :name="name"
          v-bind="slotProps ?? {}"
        />
      </template>
    </STableCards>
  </div>
</template>

<style scoped>
.s-table-wrap {
  width: 100%;
  overflow-x: auto;
}

.s-table-bulk {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: var(--color-info-tint);
  border: 1px solid var(--color-border);
  border-bottom: none;
  border-radius: var(--radius-md) var(--radius-md) 0 0;
}

.s-table {
  width: 100%;
  border-collapse: collapse;
  font-size: 14px;
}

/* ------- Header ------- */
.s-table__thead--sticky {
  position: sticky;
  top: 0;
  z-index: 10;
}

.s-table__th {
  padding: 8px 12px;
  background: var(--color-surface);
  font-weight: 600;
  font-size: 12px;
  text-transform: uppercase;
  color: var(--color-muted);
  text-align: left;
  white-space: nowrap;
  user-select: none;
  border-bottom: 1px solid var(--color-border);
}

.s-table__th--sortable {
  cursor: pointer;
}

.s-table__th--checkbox,
.s-table__td--checkbox {
  width: 44px;
  text-align: center;
}

.s-table__th--actions,
.s-table__td--actions {
  width: 1%;
  white-space: nowrap;
  text-align: right;
}

.s-table__th-content {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}

.s-table__sort-icon {
  width: 16px;
  height: 16px;
  color: var(--color-muted);
  flex-shrink: 0;
}

.s-table__sort-icon--active {
  color: var(--color-accent);
}

/* ------- Body rows ------- */
.s-table__row {
  background: var(--color-bg);
  transition: background var(--transition-fast);
}

.s-table__row--clickable {
  cursor: pointer;
}

.s-table__row:hover {
  background: var(--color-surface);
}

.s-table__row--selected {
  background: var(--color-info-tint);
}

.s-table__td {
  padding: 8px 12px;
  border-bottom: 1px solid var(--color-border);
  color: var(--color-fg);
}

/* ------- Checkbox ------- */
.s-table__checkbox-label {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  cursor: pointer;
  min-width: 44px;
  min-height: 44px;
}

.s-table__checkbox {
  width: 16px;
  height: 16px;
  accent-color: var(--color-accent);
  cursor: pointer;
  margin: 0;
}

/* ------- Skeleton loading ------- */
.s-table__skeleton {
  display: inline-block;
  border-radius: var(--radius-sm);
  background: var(--color-neutral-tint);
  animation: s-table-pulse 1.5s ease-in-out infinite;
}

@keyframes s-table-pulse {
  0%,
  100% {
    opacity: 0.4;
  }
  50% {
    opacity: 1;
  }
}

/* ------- Empty state ------- */
.s-table__empty {
  padding: 48px 16px;
  text-align: center;
}

.s-table__empty-inner {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
}

.s-table__empty-title {
  font-size: 16px;
  font-weight: 500;
  color: var(--color-fg);
  margin: 0;
}

.s-table__empty-desc {
  font-size: 14px;
  color: var(--color-muted);
  margin: 0;
}

/* ------- sr-only helper ------- */
.sr-only {
  position: absolute;
  width: 1px;
  height: 1px;
  padding: 0;
  margin: -1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
  border: 0;
}
</style>
