<script setup lang="ts">
import { ref, type Component } from 'vue'

interface TabItem {
  key: string
  label: string
  icon?: Component
  badge?: string | number
  disabled?: boolean
  /** Overrides the tab button's accessible name (e.g. to include the badge count). */
  ariaLabel?: string
  /** Announce badge changes to assistive tech while the tab is unfocused. */
  badgeLive?: boolean
}

const props = withDefaults(
  defineProps<{
    modelValue: string
    tabs: TabItem[]
    /** Fill the parent's height, keeping the tab strip fixed while the active
     *  panel scrolls.
     *
     *  Opt-in rather than the default: five of the six call sites sit in normal
     *  document flow inside a scrolling page, where a height of 100% and an
     *  internal scroll region would be wrong. Only a pane that is itself a sized
     *  box (the chatroom rail) wants this. */
    fill?: boolean
  }>(),
  { fill: false },
)

const emit = defineEmits<{
  'update:modelValue': [value: string]
}>()

const tabRefs = ref<HTMLElement[]>([])

function setTabRef(el: unknown, index: number) {
  if (el instanceof HTMLElement) {
    tabRefs.value[index] = el
  }
}

function selectTab(key: string) {
  emit('update:modelValue', key)
}

function getEnabledTabs(): number[] {
  return props.tabs
    .map((tab, i) => ({ i, disabled: tab.disabled }))
    .filter((t) => !t.disabled)
    .map((t) => t.i)
}

function onKeydown(e: KeyboardEvent) {
  const enabled = getEnabledTabs()
  if (enabled.length === 0) return

  const currentIndex = props.tabs.findIndex((t) => t.key === props.modelValue)
  const currentPos = enabled.indexOf(currentIndex)
  let nextPos = currentPos

  if (e.key === 'ArrowRight') {
    e.preventDefault()
    nextPos = currentPos < enabled.length - 1 ? currentPos + 1 : 0
  } else if (e.key === 'ArrowLeft') {
    e.preventDefault()
    nextPos = currentPos > 0 ? currentPos - 1 : enabled.length - 1
  } else if (e.key === 'Home') {
    e.preventDefault()
    nextPos = 0
  } else if (e.key === 'End') {
    e.preventDefault()
    nextPos = enabled.length - 1
  } else {
    return
  }

  const nextIndex = enabled[nextPos]
  if (nextIndex === undefined) return
  const nextTab = props.tabs[nextIndex]
  if (!nextTab) return
  selectTab(nextTab.key)
  tabRefs.value[nextIndex]?.focus()
}
</script>

<template>
  <div
    class="s-tabs"
    :class="{ 's-tabs--fill': fill }"
  >
    <div
      class="s-tabs__list"
      role="tablist"
      tabindex="-1"
      @keydown="onKeydown"
    >
      <button
        v-for="(tab, index) in tabs"
        :key="tab.key"
        :ref="(el) => setTabRef(el, index)"
        role="tab"
        type="button"
        class="s-tabs__tab"
        :class="{
          's-tabs__tab--active': modelValue === tab.key,
          's-tabs__tab--disabled': tab.disabled,
        }"
        :aria-selected="modelValue === tab.key"
        v-bind="tab.ariaLabel ? { 'aria-label': tab.ariaLabel } : {}"
        :aria-controls="`tabpanel-${tab.key}`"
        :tabindex="modelValue === tab.key ? 0 : -1"
        :disabled="tab.disabled ?? false"
        @click="!tab.disabled && selectTab(tab.key)"
      >
        <component
          :is="tab.icon"
          v-if="tab.icon"
          class="s-tabs__icon"
        />
        <span>{{ tab.label }}</span>
        <span
          v-if="tab.badge != null"
          class="s-tabs__badge"
        >
          {{ tab.badge }}
        </span>
        <!-- W-7: a persistent polite live region announces badge changes even
             when the visible badge is added/removed (a newly-inserted live
             region is not reliably announced). Visually hidden; the button's
             aria-label carries the count on focus. -->
        <span
          v-if="tab.badgeLive"
          class="s-tabs__badge-live"
          aria-live="polite"
        >{{ tab.badge ?? '' }}</span>
      </button>
    </div>
    <div class="s-tabs__panels">
      <div
        v-for="tab in tabs"
        :id="`tabpanel-${tab.key}`"
        :key="tab.key"
        role="tabpanel"
        :aria-labelledby="tab.key"
        :hidden="modelValue !== tab.key"
      >
        <slot
          v-if="modelValue === tab.key"
          :name="`tab-${tab.key}`"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.s-tabs {
  display: flex;
  flex-direction: column;
}

.s-tabs__list {
  display: flex;
  border-bottom: 1px solid var(--color-border-subtle);
  /* Scrollable when tabs overflow (mobile); no-op when they fit (desktop). */
  overflow-x: auto;
  scrollbar-width: thin;
}

.s-tabs__tab {
  display: inline-flex;
  align-items: center;
  gap: var(--space-1-5);
  height: var(--control-h-md);
  padding: 0 var(--space-4);
  flex-shrink: 0;
  background: none;
  border: none;
  border-bottom: 2px solid transparent;
  margin-bottom: -1px;
  color: var(--color-muted);
  font-size: var(--font-size-sm);
  font-weight: var(--weight-medium);
  cursor: pointer;
  white-space: nowrap;
  transition:
    color var(--transition-fast),
    border-color var(--transition-fast);
}

.s-tabs__tab:hover:not(.s-tabs__tab--active):not(.s-tabs__tab--disabled) {
  color: var(--color-fg);
}

.s-tabs__tab--active {
  color: var(--color-accent);
  border-bottom-color: var(--color-accent);
}

.s-tabs__tab--disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.s-tabs__icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.s-tabs__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  padding: 0 var(--space-1-5);
  min-width: 18px;
  height: 18px;
  font-size: 0.7rem;
  font-weight: var(--weight-semibold);
  line-height: var(--line-none);
  border-radius: var(--radius-full);
  background: var(--color-surface);
  color: var(--color-muted);
}

.s-tabs__badge-live {
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

.s-tabs__panels {
  margin-top: 0;
}

/* Fill mode. `min-height: 0` on both the root and the panel area is the
   load-bearing part: without it a flex item refuses to shrink below its content
   size, so tall panel content pushes the whole component past its parent instead
   of scrolling inside it. */
.s-tabs--fill {
  height: 100%;
  min-height: 0;
}

.s-tabs--fill .s-tabs__list {
  flex-shrink: 0;
}

.s-tabs--fill .s-tabs__panels {
  flex: 1 1 auto;
  min-height: 0;
}

/* Deliberately no `overflow` here. Each panel owns its own scroll region
   (ChatroomPresence and ObserverPanel already declare one); adding a second
   around them would nest scrollbars. This rule exists so those declarations
   have a definite height to resolve `height: 100%` against. */
.s-tabs--fill .s-tabs__panels > [role='tabpanel'] {
  height: 100%;
}
</style>
