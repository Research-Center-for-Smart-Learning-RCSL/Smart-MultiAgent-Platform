import { ref, computed, watch, onMounted, onUnmounted } from 'vue'

const STORAGE_PREFIX = 'smap:canvas:'
const DEFAULT_WIDTH_FRACTION = 0.45
const MIN_WIDTH_PX = 300

export function useCanvasSplitPane(chatroomId: () => string) {
  const isOpen = ref(false)
  const isFullscreen = ref(false)
  const widthFraction = ref(DEFAULT_WIDTH_FRACTION)
  const isDragging = ref(false)

  function storageKey() {
    return `${STORAGE_PREFIX}${chatroomId()}`
  }

  function loadState() {
    try {
      const raw = localStorage.getItem(storageKey())
      if (raw) {
        const state = JSON.parse(raw)
        isOpen.value = state.open ?? false
        widthFraction.value = state.width ?? DEFAULT_WIDTH_FRACTION
      }
    } catch {
      // localStorage may be unavailable
    }
  }

  function saveState() {
    try {
      localStorage.setItem(
        storageKey(),
        JSON.stringify({ open: isOpen.value, width: widthFraction.value }),
      )
    } catch {
      // localStorage may be unavailable
    }
  }

  function toggle() {
    isOpen.value = !isOpen.value
    if (!isOpen.value) {
      isFullscreen.value = false
    }
    saveState()
  }

  function toggleFullscreen() {
    isFullscreen.value = !isFullscreen.value
  }

  function close() {
    isOpen.value = false
    isFullscreen.value = false
    saveState()
  }

  function startDrag(e: MouseEvent) {
    e.preventDefault()
    isDragging.value = true

    const onMove = (ev: MouseEvent) => {
      const containerWidth = document.documentElement.clientWidth
      const newFraction = 1 - ev.clientX / containerWidth
      widthFraction.value = Math.max(MIN_WIDTH_PX / containerWidth, Math.min(0.7, newFraction))
    }

    const onUp = () => {
      isDragging.value = false
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      saveState()
    }

    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }

  const canvasWidthPercent = computed(() => `${widthFraction.value * 100}%`)

  onMounted(() => loadState())

  watch(() => chatroomId(), () => loadState())

  return {
    isOpen,
    isFullscreen,
    isDragging,
    canvasWidthPercent,
    toggle,
    toggleFullscreen,
    close,
    startDrag,
  }
}
