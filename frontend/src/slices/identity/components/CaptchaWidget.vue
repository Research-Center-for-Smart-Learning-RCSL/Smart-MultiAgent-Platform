<script setup lang="ts">
import { onMounted, onUnmounted, watch, ref } from 'vue'

const props = defineProps<{
  provider: 'hcaptcha' | 'turnstile' | 'off'
  sitekey: string
}>()
const emit = defineEmits<{ (e: 'update:token', token: string): void }>()

const container = ref<HTMLElement | null>(null)

interface CaptchaApi {
  render: (
    el: HTMLElement,
    opts: {
      sitekey: string
      callback: (token: string) => void
      'expired-callback'?: () => void
      'error-callback'?: () => void
    },
  ) => string | number
  remove?: (widgetId: string | number) => void
  reset?: (widgetId: string | number) => void
}

const SCRIPTS: Record<string, { src: string; global: string }> = {
  hcaptcha: { src: 'https://js.hcaptcha.com/1/api.js?render=explicit', global: 'hcaptcha' },
  turnstile: {
    src: 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit',
    global: 'turnstile',
  },
}

// The id returned by render(), plus the global whose .remove() can clean it up.
// Tracking both lets us tear the widget down before re-rendering — otherwise the
// provider throws "captcha already rendered into this element".
let widgetId: string | number | null = null
let widgetGlobal: string | null = null

function getApi(global: string): CaptchaApi | undefined {
  return (window as unknown as Record<string, CaptchaApi | undefined>)[global]
}

function destroyWidget(): void {
  if (widgetId === null || widgetGlobal === null) return
  const api = getApi(widgetGlobal)
  try {
    api?.remove?.(widgetId)
  } catch {
    // Best-effort: the provider may have already torn it down (e.g. on script
    // reload). Fall through to clearing the container so a re-render is clean.
  }
  if (container.value) container.value.innerHTML = ''
  widgetId = null
  widgetGlobal = null
}

async function renderWidget(): Promise<void> {
  // Always tear down any prior widget first so a provider/sitekey change (or an
  // async config arrival) re-renders cleanly instead of stacking widgets.
  destroyWidget()
  if (props.provider === 'off' || !props.sitekey || !container.value) return
  const meta = SCRIPTS[props.provider]
  if (!meta) return
  try {
    await loadScript(meta.src)
  } catch {
    return // network/CSP failure — leave the slot empty rather than throwing
  }
  const api = getApi(meta.global)
  if (!api || !container.value) return
  widgetId = api.render(container.value, {
    sitekey: props.sitekey,
    callback: (token: string) => emit('update:token', token),
    'expired-callback': () => emit('update:token', ''),
    'error-callback': () => emit('update:token', ''),
  })
  widgetGlobal = meta.global
}

function loadScript(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    if (document.querySelector(`script[src="${src}"]`)) {
      resolve()
      return
    }
    const s = document.createElement('script')
    s.src = src
    s.async = true
    s.defer = true
    s.onload = () => resolve()
    s.onerror = () => reject(new Error('captcha script failed to load'))
    document.head.appendChild(s)
  })
}

onMounted(renderWidget)
// Re-render if the config arrives asynchronously after first paint, or if the
// provider/sitekey changes — destroyWidget() inside renderWidget handles teardown.
watch(
  () => [props.provider, props.sitekey],
  () => {
    void renderWidget()
  },
)
// Clean up on unmount so leaving the page doesn't leak the widget/iframe.
onUnmounted(destroyWidget)
</script>

<template>
  <div
    v-if="provider !== 'off'"
    ref="container"
    class="captcha-widget"
    data-testid="captcha-widget"
  />
</template>
