<script setup lang="ts">
import { ref, computed, watch, onBeforeUnmount, nextTick, useId, type CSSProperties } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { useQuery } from '@tanstack/vue-query'
import {
  ChevronDownIcon,
  CheckIcon,
  PlusIcon,
} from '@heroicons/vue/24/outline'
import { useWorkspaceStore } from '@shared/stores/workspace'
import { useSessionStore } from '@shared/stores/session'
import {
  clampToViewport,
  useAnchoredPosition,
  VIEWPORT_MARGIN,
  type AnchoredPositionContext,
} from '@shared/composables/useAnchoredPosition'
import { tenancyKeys, orgsApi, projectsApi, type Org, type Project } from '@slices/tenancy'

const props = defineProps<{
  compact?: boolean
}>()

const { t } = useI18n()
const router = useRouter()
const workspace = useWorkspaceStore()
const session = useSessionStore()

const isOpen = ref(false)
const panelRef = ref<HTMLElement | null>(null)
const triggerRef = ref<HTMLElement | null>(null)

const orgsQuery = useQuery({
  queryKey: tenancyKeys.orgs(),
  queryFn: () => orgsApi.list(),
})

const orgs = computed(() => orgsQuery.data.value ?? [])

const projectsScope = computed(() =>
  workspace.orgId
    ? { scope: 'org' as const, id: workspace.orgId }
    : { scope: 'user' as const, id: session.me?.id ?? null },
)

const projectsEnabled = computed(() => !!projectsScope.value.id)

const projectsQuery = useQuery({
  queryKey: computed(() =>
    tenancyKeys.projects(projectsScope.value.scope, projectsScope.value.id),
  ),
  queryFn: () => projectsApi.list(projectsScope.value.scope, projectsScope.value.id!),
  enabled: projectsEnabled,
})

const projects = computed(() => projectsQuery.data.value ?? [])

const displayText = computed(() => {
  if (!workspace.hasOrg) {
    return workspace.hasProject
      ? `${t('app.switcher.personal')} / ${workspace.projectName}`
      : t('app.switcher.personal')
  }
  if (!workspace.hasProject) return workspace.orgName
  return `${workspace.orgName} / ${workspace.projectName}`
})

function toggle() {
  isOpen.value = !isOpen.value
}

function close() {
  isOpen.value = false
}

function selectOrg(org: Org) {
  workspace.selectOrg(org.id, org.name)
}

function selectPersonal() {
  workspace.clear()
}

function selectProject(project: Project) {
  workspace.selectProject(project.id, project.name)
  close()
}

function goCreateOrg() {
  close()
  router.push('/orgs')
}

function goCreateProject() {
  close()
  router.push({
    name: 'tenancy.projectList',
    query: {
      scope: workspace.orgId ?? 'personal',
      create: '1',
    },
  })
}

function onClickOutside(e: MouseEvent) {
  const target = e.target as Node
  if (
    triggerRef.value &&
    !triggerRef.value.contains(target) &&
    panelRef.value &&
    !panelRef.value.contains(target)
  ) {
    close()
  }
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === 'Escape') {
    close()
    triggerRef.value?.focus()
  }
}

// Links the trigger to the relocated panel: teleporting moves the panel to
// the end of body, so the DOM adjacency assistive tech would otherwise rely
// on is gone.
const panelId = useId()

// The panel is teleported to body: the topbar is a sticky stacking context at
// --z-topbar, so a panel left inside it is capped at 200 against root-context
// overlays (the chatroom search panel and compact rail overlays at
// --z-dropdown painted over it). Placement math mirrors SDropdown: prefer
// below, flip above when below cannot hold the content and above has more
// room, cap the height, and pin to the viewport only when neither side fits.
const TRIGGER_GAP = 4
const MIN_PANEL_HEIGHT = 160
const MAX_PANEL_HEIGHT = 400

function computePanelPosition(ctx: AnchoredPositionContext): CSSProperties {
  const { rect, panel, viewportWidth, viewportHeight } = ctx
  const roomBelow = viewportHeight - rect.bottom - TRIGGER_GAP - VIEWPORT_MARGIN
  const roomAbove = rect.top - TRIGGER_GAP - VIEWPORT_MARGIN
  // scrollHeight, not the bounding box: once a cap is applied the box reports
  // the capped height, and re-evaluating on scroll would then un-flip.
  const naturalHeight = panel.scrollHeight
  const flip = naturalHeight > roomBelow && roomAbove > roomBelow
  const room = flip ? roomAbove : roomBelow
  const maxHeight = Math.min(
    Math.max(Math.min(room, MAX_PANEL_HEIGHT), MIN_PANEL_HEIGHT),
    viewportHeight - VIEWPORT_MARGIN * 2,
  )

  const pos: CSSProperties = {
    maxHeight: `${Math.round(maxHeight)}px`,
    left: `${Math.round(clampToViewport(rect.left, panel.offsetWidth, viewportWidth))}px`,
  }
  // Compact mode's width cap lives in CSS (the sidebar-width rule below) and
  // an inline max-width would override it; the viewport-wide cap is only for
  // the full-width panel.
  if (!props.compact) {
    pos.maxWidth = `${viewportWidth - VIEWPORT_MARGIN * 2}px`
  }
  if (maxHeight > room) {
    // The floor won on both sides: anchoring would push the tail off screen,
    // so pin to the bottom edge and let the panel scroll.
    pos.top = `${Math.max(VIEWPORT_MARGIN, viewportHeight - VIEWPORT_MARGIN - maxHeight)}px`
  } else if (flip) {
    pos.bottom = `${Math.round(viewportHeight - rect.top + TRIGGER_GAP)}px`
  } else {
    pos.top = `${Math.round(rect.bottom + TRIGGER_GAP)}px`
  }
  return pos
}

const { style: panelPos } = useAnchoredPosition({
  anchor: triggerRef,
  panel: panelRef,
  open: isOpen,
  compute: computePanelPosition,
  // The sidebar scrolls its nav, so the trigger can leave its scrollport with
  // the panel open; a fixed panel would chase it over the topbar otherwise.
  onAnchorClipped: close,
})

// Teleporting also breaks the tab sequence: the panel sits at the end of
// body, so tabbing past its last action would land in browser chrome while
// the panel silently stayed open. Close as soon as focus leaves the panel
// for anywhere other than the trigger.
function onPanelFocusout(e: FocusEvent) {
  const next = e.relatedTarget as Node | null
  if (next && (panelRef.value?.contains(next) || triggerRef.value?.contains(next))) return
  close()
}

watch(isOpen, async (open) => {
  if (open) {
    document.addEventListener('click', onClickOutside, { capture: true })
    await nextTick()
    panelRef.value?.focus()
  } else {
    document.removeEventListener('click', onClickOutside, { capture: true })
  }
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onClickOutside, { capture: true })
})
</script>

<template>
  <div
    class="switcher"
    :class="{ 'switcher--compact': compact }"
    role="none"
    @keydown="onKeydown"
  >
    <button
      ref="triggerRef"
      class="switcher__trigger"
      :class="{
        'switcher__trigger--compact': compact,
      }"
      type="button"
      :aria-expanded="isOpen"
      :aria-controls="isOpen ? panelId : undefined"
      :aria-label="t('app.switcher.placeholder')"
      @click.stop="toggle"
    >
      <span class="switcher__text">
        {{ displayText }}
      </span>
      <ChevronDownIcon
        class="switcher__chevron"
        :class="{ 'switcher__chevron--open': isOpen }"
      />
    </button>

    <Teleport to="body">
      <Transition name="switcher-panel">
        <!-- Teleported, so the topbar's sticky stacking context cannot cap it
             and the sidebar's scrollport cannot clip it. Keydown is bound here
             too: native events bubble through the real DOM (body), not the
             virtual parent, so the wrapper's handler never sees panel keys. -->
        <div
          v-if="isOpen"
          :id="panelId"
          ref="panelRef"
          class="switcher__panel"
          :class="{ 'switcher__panel--compact': compact }"
          :style="panelPos"
          role="none"
          tabindex="-1"
          @keydown="onKeydown"
          @focusout="onPanelFocusout"
        >
          <!-- Organizations section -->
          <div class="switcher__section-header">
            {{ t('app.switcher.orgs') }}
          </div>
          <div
            v-if="orgsQuery.isError.value"
            class="switcher__error"
          >
            {{ t('app.switcher.loadError') }}
          </div>
          <ul
            v-else
            class="switcher__list"
            role="listbox"
          >
            <li
              class="switcher__item"
              :class="{ 'switcher__item--active': !workspace.hasOrg }"
              role="option"
              tabindex="0"
              :aria-selected="!workspace.hasOrg"
              @click="selectPersonal"
              @keydown.enter="selectPersonal"
              @keydown.space.prevent="selectPersonal"
            >
              <span class="switcher__item-label">{{ t('app.switcher.personal') }}</span>
              <CheckIcon
                v-if="!workspace.hasOrg"
                class="switcher__check"
              />
            </li>
            <li
              v-for="org in orgs"
              :key="org.id"
              class="switcher__item"
              :class="{ 'switcher__item--active': org.id === workspace.orgId }"
              role="option"
              tabindex="0"
              :aria-selected="org.id === workspace.orgId"
              @click="selectOrg(org)"
              @keydown.enter="selectOrg(org)"
              @keydown.space.prevent="selectOrg(org)"
            >
              <span class="switcher__item-label">{{ org.name }}</span>
              <CheckIcon
                v-if="org.id === workspace.orgId"
                class="switcher__check"
              />
            </li>
          </ul>
          <button
            class="switcher__action"
            type="button"
            @click="goCreateOrg"
          >
            <PlusIcon class="switcher__action-icon" />
            {{ t('app.switcher.createOrg') }}
          </button>

          <!-- Projects section (shown for the active org or personal scope) -->
          <template v-if="projectsEnabled">
            <div class="switcher__divider" />
            <div class="switcher__section-header">
              {{ t('app.switcher.projects') }}
            </div>
            <div
              v-if="projectsQuery.isError.value"
              class="switcher__error"
            >
              {{ t('app.switcher.loadError') }}
            </div>
            <ul
              v-else
              class="switcher__list"
              role="listbox"
            >
              <li
                v-for="project in projects"
                :key="project.id"
                class="switcher__item"
                :class="{
                  'switcher__item--active':
                    project.id === workspace.projectId,
                }"
                role="option"
                tabindex="0"
                :aria-selected="project.id === workspace.projectId"
                @click="selectProject(project)"
                @keydown.enter="selectProject(project)"
                @keydown.space.prevent="selectProject(project)"
              >
                <span class="switcher__item-label">{{ project.name }}</span>
                <CheckIcon
                  v-if="project.id === workspace.projectId"
                  class="switcher__check"
                />
              </li>
            </ul>
            <button
              class="switcher__action"
              type="button"
              @click="goCreateProject"
            >
              <PlusIcon class="switcher__action-icon" />
              {{ t('app.switcher.createProject') }}
            </button>
          </template>
        </div>
      </Transition>
    </Teleport>
  </div>
</template>

<style scoped>
.switcher {
  position: relative;
  display: inline-flex;
}

.switcher__trigger {
  display: inline-flex;
  align-items: center;
  gap: var(--space-2);
  padding: var(--space-1-5) var(--space-3);
  background: none;
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  color: var(--color-fg);
  font-size: var(--font-size-sm);
  font-weight: var(--weight-medium);
  line-height: var(--line-none);
  cursor: pointer;
  white-space: nowrap;
  max-width: 280px;
  transition:
    background var(--transition-fast),
    border-color var(--transition-fast);
}

.switcher__trigger:hover {
  background: var(--color-surface);
}

.switcher__trigger--compact {
  max-width: 180px;
}

/* In the sidebar the switcher hugs the screen's left edge; sizing from the
   content instead of the 280px floor keeps the panel narrow there, and the
   sidebar-width cap is what makes the nowrap+ellipsis item labels actually
   truncate — without it one long org name sizes the panel across the main
   content (the JS maxWidth only stops it at the viewport edge). A modifier
   class rather than a descendant selector: the panel is teleported to body,
   so it is no longer a DOM descendant of .switcher--compact; --sidebar-width
   is defined on :root, so it still resolves there. */
.switcher__panel--compact {
  min-width: 0;
  width: max-content;
  max-width: calc(var(--sidebar-width) - 24px);
}

.switcher__text {
  overflow: hidden;
  text-overflow: ellipsis;
}

.switcher__chevron {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  color: var(--color-muted);
  transition: transform var(--transition-fast);
}

.switcher__chevron--open {
  transform: rotate(180deg);
}

.switcher__panel {
  /* Coordinates and the height/width caps come from updatePanelPosition;
     declared fixed here so the panel is never laid out in body flow on the
     frame before it is measured. */
  position: fixed;
  min-width: 280px;
  overflow-y: auto;
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-lg);
  z-index: var(--z-dropdown);
  padding: var(--space-1) 0;
}

.switcher__panel:focus {
  outline: none;
}

.switcher__section-header {
  padding: var(--space-2) var(--space-4) var(--space-1);
  font-size: 0.6875rem;
  font-weight: var(--weight-semibold);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  color: var(--color-muted);
  user-select: none;
}

.switcher__list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.switcher__item {
  display: flex;
  align-items: center;
  justify-content: space-between;
  height: 36px;
  padding: 0 var(--space-4);
  font-size: var(--font-size-sm);
  color: var(--color-fg);
  cursor: pointer;
  transition: background var(--transition-fast);
}

.switcher__item:hover {
  background: var(--color-surface);
}

.switcher__item--active {
  color: var(--color-accent);
  font-weight: var(--weight-medium);
}

.switcher__item-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.switcher__check {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
  color: var(--color-accent);
}

.switcher__action {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  width: 100%;
  height: 36px;
  padding: 0 var(--space-4);
  background: none;
  border: none;
  color: var(--color-muted);
  font-size: var(--font-size-code);
  cursor: pointer;
  transition:
    background var(--transition-fast),
    color var(--transition-fast);
}

.switcher__action:hover {
  background: var(--color-surface);
  color: var(--color-fg);
}

.switcher__action-icon {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.switcher__error {
  padding: var(--space-2) var(--space-4);
  font-size: var(--font-size-code);
  color: var(--color-danger);
}

.switcher__divider {
  height: 1px;
  margin: var(--space-1) 0;
  background: var(--color-border-subtle);
}

/* -- Enter/Leave transitions -- */
.switcher-panel-enter-active,
.switcher-panel-leave-active {
  transition:
    opacity var(--transition-fast),
    transform var(--transition-fast);
}

.switcher-panel-enter-from,
.switcher-panel-leave-to {
  opacity: 0;
  transform: translateY(-4px);
}
</style>
