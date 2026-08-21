<script setup lang="ts">
import { ref, watch, nextTick, onMounted, onBeforeUnmount, type Component, type CSSProperties } from 'vue'

interface DropdownItem {
  key: string
  label: string
  icon?: Component
  danger?: boolean
  disabled?: boolean
  divider?: boolean
}

const props = withDefaults(defineProps<{
  items: DropdownItem[]
  placement?: 'bottom-start' | 'bottom-end'
  width?: string
}>(), {
  placement: 'bottom-end',
  width: 'auto',
})

const emit = defineEmits<{
  select: [key: string]
}>()

const isOpen = ref(false)
const triggerRef = ref<HTMLElement | null>(null)
const menuRef = ref<HTMLElement | null>(null)
const itemRefs = ref<HTMLElement[]>([])
const menuPos = ref<CSSProperties>({})

function setItemRef(el: unknown, index: number) {
  if (el instanceof HTMLElement) {
    itemRefs.value[index] = el
  }
}

// The trigger slot always holds a real control (SButton/anchor), but it is
// caller-provided so we cannot bind ARIA to it declaratively from this template.
// Imperatively set the menu-popup ARIA on that control (not the presentational
// wrapper, where role="none" would discard it) and keep aria-expanded in sync.
function syncTriggerAria() {
  const control = triggerRef.value?.querySelector<HTMLElement>(
    'button, [role="button"], a[href]',
  )
  if (!control) return
  control.setAttribute('aria-haspopup', 'menu')
  control.setAttribute('aria-expanded', String(isOpen.value))
}

function toggle() {
  isOpen.value = !isOpen.value
}

function close() {
  isOpen.value = false
}

function selectItem(item: DropdownItem) {
  if (item.disabled || item.divider) return
  emit('select', item.key)
  close()
}

function getActionableIndices(): number[] {
  return props.items
    .map((item, i) => ({ i, skip: item.divider || item.disabled }))
    .filter((x) => !x.skip)
    .map((x) => x.i)
}

function onKeydown(e: KeyboardEvent) {
  if (!isOpen.value) return

  const actionable = getActionableIndices()
  if (actionable.length === 0) return

  if (e.key === 'Escape') {
    e.preventDefault()
    close()
    triggerRef.value?.focus()
    return
  }

  const currentEl = document.activeElement
  const currentIndex = itemRefs.value.findIndex((el) => el === currentEl)
  const currentPos = actionable.indexOf(currentIndex)

  if (e.key === 'ArrowDown') {
    e.preventDefault()
    const nextPos = currentPos < actionable.length - 1 ? currentPos + 1 : 0
    const nextIndex = actionable[nextPos]
    if (nextIndex !== undefined) {
      itemRefs.value[nextIndex]?.focus()
    }
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    const prevPos = currentPos > 0 ? currentPos - 1 : actionable.length - 1
    const prevIndex = actionable[prevPos]
    if (prevIndex !== undefined) {
      itemRefs.value[prevIndex]?.focus()
    }
  } else if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    const currentItem = currentIndex >= 0 ? props.items[currentIndex] : undefined
    if (currentItem) {
      selectItem(currentItem)
    }
  }
}

// Gap between trigger and menu, and the clearance kept from the viewport edge.
const TRIGGER_GAP = 4
const VIEWPORT_MARGIN = 8
// Floor for the height cap. On a viewport too short for either side to hold a
// usable menu, a cap derived purely from the available space would collapse it
// to nothing; a scrollable stub of a few rows is the lesser evil.
const MIN_MENU_HEIGHT = 96

function updateMenuPosition() {
  if (!triggerRef.value) return
  const rect = triggerRef.value.getBoundingClientRect()
  const viewportHeight = window.innerHeight
  const spaceBelow = viewportHeight - rect.bottom - TRIGGER_GAP - VIEWPORT_MARGIN
  const spaceAbove = rect.top - TRIGGER_GAP - VIEWPORT_MARGIN
  // scrollHeight, not the bounding box: once a cap is applied the box reports
  // the capped height, and re-evaluating on scroll would then read the menu as
  // fitting and un-flip it.
  const naturalHeight = menuRef.value?.scrollHeight ?? 0

  // Pick the side first, then cap to it. Flipping only when the menu genuinely
  // does not fit below keeps the common case anchored where the user expects.
  const flip = naturalHeight > spaceBelow && spaceAbove > spaceBelow
  const room = flip ? spaceAbove : spaceBelow
  const available = Math.min(
    Math.max(room, MIN_MENU_HEIGHT),
    viewportHeight - VIEWPORT_MARGIN * 2,
  )

  const pos: CSSProperties = {
    position: 'fixed',
    maxHeight: `${Math.round(available)}px`,
  }
  if (available > room) {
    // The floor won: anchoring to the trigger would push the far edge off
    // screen, which is the unreachable-items defect this function exists to
    // prevent. Pin to the viewport instead and let the menu scroll.
    pos.top = `${VIEWPORT_MARGIN}px`
  } else if (flip) {
    pos.bottom = `${viewportHeight - rect.top + TRIGGER_GAP}px`
  } else {
    pos.top = `${rect.bottom + TRIGGER_GAP}px`
  }
  if (props.placement === 'bottom-end') {
    pos.right = `${window.innerWidth - rect.right}px`
  } else {
    pos.left = `${rect.left}px`
  }
  menuPos.value = pos
}

function onScrollWhileOpen() {
  if (isOpen.value) updateMenuPosition()
}

function onClickOutside(e: MouseEvent) {
  const target = e.target as Node
  if (
    triggerRef.value && !triggerRef.value.contains(target) &&
    menuRef.value && !menuRef.value.contains(target)
  ) {
    close()
  }
}

onMounted(syncTriggerAria)

watch(isOpen, async (open) => {
  syncTriggerAria()
  if (open) {
    document.addEventListener('click', onClickOutside, { capture: true })
    window.addEventListener('scroll', onScrollWhileOpen, { capture: true, passive: true })
    window.addEventListener('resize', onScrollWhileOpen, { passive: true })
    await nextTick()
    // After the menu exists: both the flip and the cap need its measured
    // height. The enter transition starts at opacity 0 and nextTick is a
    // microtask, so no unpositioned frame is ever painted.
    updateMenuPosition()
    const actionable = getActionableIndices()
    const firstIndex = actionable[0]
    if (firstIndex !== undefined) {
      itemRefs.value[firstIndex]?.focus()
    }
  } else {
    document.removeEventListener('click', onClickOutside, { capture: true })
    window.removeEventListener('scroll', onScrollWhileOpen, { capture: true })
    window.removeEventListener('resize', onScrollWhileOpen)
    itemRefs.value = []
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onClickOutside, { capture: true })
  window.removeEventListener('scroll', onScrollWhileOpen, { capture: true })
  window.removeEventListener('resize', onScrollWhileOpen)
})
</script>

<template>
  <div
    class="s-dropdown"
    role="none"
    @keydown="onKeydown"
  >
    <div
      ref="triggerRef"
      class="s-dropdown__trigger"
      role="none"
      @click.stop="toggle"
      @keydown.enter.stop="toggle"
    >
      <slot name="trigger" />
    </div>
    <Teleport to="body">
      <Transition name="s-dropdown">
        <div
          v-if="isOpen"
          ref="menuRef"
          class="s-dropdown__menu"
          :style="{ ...menuPos, width: width, minWidth: '180px' }"
          role="menu"
        >
          <template
            v-for="(item, index) in items"
            :key="item.key"
          >
            <div
              v-if="item.divider"
              class="s-dropdown__divider"
              role="separator"
            />
            <button
              v-else
              :ref="(el) => setItemRef(el, index)"
              class="s-dropdown__item"
              :class="{
                's-dropdown__item--danger': item.danger,
                's-dropdown__item--disabled': item.disabled,
              }"
              role="menuitem"
              type="button"
              :disabled="item.disabled ?? false"
              tabindex="-1"
              @click.stop="selectItem(item)"
            >
              <component
                :is="item.icon"
                v-if="item.icon"
                class="s-dropdown__item-icon"
                :class="{ 's-dropdown__item-icon--danger': item.danger }"
              />
              <span>{{ item.label }}</span>
            </button>
          </template>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.s-dropdown {
  position: relative;
  display: inline-flex;
}

.s-dropdown__trigger {
  display: inline-flex;
}

.s-dropdown__menu {
  /* Also set inline by updateMenuPosition; declared here so the menu is never
     laid out in body flow on the frame before it is measured. */
  position: fixed;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  padding: 4px 0;
  /* Pairs with the max-height updateMenuPosition sets: a menu that fits on
     neither side stays reachable by scrolling instead of running off screen. */
  overflow-y: auto;
  z-index: var(--z-dropdown);
}

.s-dropdown__divider {
  height: 1px;
  background: var(--color-border);
  margin: 4px 0;
}

.s-dropdown__item {
  display: flex;
  align-items: center;
  gap: 8px;
  width: 100%;
  height: 36px;
  padding: 0 16px;
  background: none;
  border: none;
  color: var(--color-fg);
  font-size: 0.875rem;
  text-align: left;
  cursor: pointer;
  white-space: nowrap;
  transition: background var(--transition-fast);
}

.s-dropdown__item:hover:not(.s-dropdown__item--disabled),
.s-dropdown__item:focus:not(.s-dropdown__item--disabled) {
  background: var(--color-surface);
  outline: none;
}

.s-dropdown__item--danger {
  color: var(--color-danger);
}

.s-dropdown__item--disabled {
  opacity: 0.5;
  cursor: not-allowed;
}

.s-dropdown__item-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  color: var(--color-muted);
}

.s-dropdown__item-icon--danger {
  color: var(--color-danger);
}

/* -- Enter/Leave transitions -- */
.s-dropdown-enter-active,
.s-dropdown-leave-active {
  transition:
    opacity var(--transition-fast),
    transform var(--transition-fast);
}

.s-dropdown-enter-from,
.s-dropdown-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
