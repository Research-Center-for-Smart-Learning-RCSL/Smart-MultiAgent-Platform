<script setup lang="ts">
import { onMounted, watch, ref } from 'vue'

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
}

const SCRIPTS: Record<string, { src: string; global: string }> = {
  hcaptcha: { src: 'https://js.hcaptcha.com/1/api.js?render=explicit', global: 'hcaptcha' },
  turnstile: {
    src: 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit',
    global: 'turnstile',
  },
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

async function renderWidget(): Promise<void> {
  if (props.provider === 'off' || !props.sitekey || !container.value) return
  const meta = SCRIPTS[props.provider]
  if (!meta) return
  try {
    await loadScript(meta.src)
  } catch {
    return // network/CSP failure — leave the slot empty rather than throwing
  }
  const api = (window as unknown as Record<string, CaptchaApi | undefined>)[meta.global]
  if (!api || !container.value) return
  api.render(container.value, {
    sitekey: props.sitekey,
    callback: (token: string) => emit('update:token', token),
    'expired-callback': () => emit('update:token', ''),
    'error-callback': () => emit('update:token', ''),
  })
}

onMounted(renderWidget)
// Re-render if the config arrives asynchronously after first paint.
watch(
  () => [props.provider, props.sitekey],
  () => {
    void renderWidget()
  },
)
</script>

<template>
  <div
    v-if="provider !== 'off'"
    ref="container"
    class="captcha-widget"
    data-testid="captcha-widget"
  />
</template>
