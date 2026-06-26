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
    itemRefs.value[actionable[nextPos]]?.focus()
  } else if (e.key === 'ArrowUp') {
    e.preventDefault()
    const prevPos = currentPos > 0 ? currentPos - 1 : actionable.length - 1
    itemRefs.value[actionable[prevPos]]?.focus()
  } else if (e.key === 'Enter' || e.key === ' ') {
    e.preventDefault()
    if (currentIndex >= 0) {
      selectItem(props.items[currentIndex])
    }
  }
}

function updateMenuPosition() {
  if (!triggerRef.value) return
  const rect = triggerRef.value.getBoundingClientRect()
  const pos: CSSProperties = {
    position: 'fixed',
    top: `${rect.bottom + 4}px`,
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
    updateMenuPosition()
    document.addEventListener('click', onClickOutside, { capture: true })
    window.addEventListener('scroll', onScrollWhileOpen, { capture: true, passive: true })
    window.addEventListener('resize', onScrollWhileOpen, { passive: true })
    await nextTick()
    const actionable = getActionableIndices()
    if (actionable.length > 0) {
      itemRefs.value[actionable[0]]?.focus()
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
              :disabled="item.disabled"
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
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  padding: 4px 0;
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
    opacity var(--transition-fast) ease,
    transform var(--transition-fast) ease;
}

.s-dropdown-enter-from,
.s-dropdown-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
