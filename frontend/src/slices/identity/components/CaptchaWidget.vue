<script setup lang="ts">
import { onMounted, onUnmounted, watch, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { SLoadingSpinner } from '@shared/ui'

const { t } = useI18n()

const props = defineProps<{
  provider: 'hcaptcha' | 'turnstile' | 'off'
  sitekey: string
}>()
const emit = defineEmits<{ (e: 'update:token', token: string): void }>()

const container = ref<HTMLElement | null>(null)
const scriptLoading = ref(false)
const scriptFailed = ref(false)

interface CaptchaApi {
  render: (
    el: HTMLElement,
    opts: {
      sitekey: string
      theme?: string
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

let widgetId: string | number | null = null
let widgetGlobal: string | null = null

function getApi(global: string): CaptchaApi | undefined {
  return (window as unknown as Record<string, CaptchaApi | undefined>)[global]
}

function isDarkMode(): boolean {
  return document.documentElement.getAttribute('data-theme') === 'dark'
}

function destroyWidget(): void {
  if (widgetId === null || widgetGlobal === null) return
  const api = getApi(widgetGlobal)
  try {
    api?.remove?.(widgetId)
  } catch {
    // Best-effort cleanup
  }
  if (container.value) container.value.innerHTML = ''
  widgetId = null
  widgetGlobal = null
}

async function renderWidget(): Promise<void> {
  destroyWidget()
  scriptFailed.value = false
  if (props.provider === 'off' || !props.sitekey || !container.value) return
  const meta = SCRIPTS[props.provider]
  if (!meta) return
  scriptLoading.value = true
  try {
    await loadScript(meta.src)
  } catch {
    scriptLoading.value = false
    scriptFailed.value = true
    return
  }
  scriptLoading.value = false
  const api = getApi(meta.global)
  if (!api || !container.value) return
  widgetId = api.render(container.value, {
    sitekey: props.sitekey,
    theme: isDarkMode() ? 'dark' : 'light',
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
watch(
  () => [props.provider, props.sitekey],
  () => { void renderWidget() },
)
onUnmounted(destroyWidget)
</script>

<template>
  <div
    v-if="provider !== 'off'"
    class="captcha-container"
    :aria-label="t('identity.register.captchaLabel')"
    data-testid="captcha-widget"
  >
    <SLoadingSpinner
      v-if="scriptLoading"
      size="sm"
      :text="t('identity.register.captchaLoading')"
    />
    <p
      v-else-if="scriptFailed"
      class="captcha-fallback"
    >
      {{ t('identity.register.captchaFallback') }}
    </p>
    <div
      v-show="!scriptLoading && !scriptFailed"
      ref="container"
      class="captcha-widget"
    />
  </div>
</template>

<style scoped>
.captcha-container {
  display: flex;
  justify-content: center;
  min-height: 78px;
  align-items: center;
}

.captcha-fallback {
  font-size: 0.75rem;
  color: var(--color-muted);
  text-align: center;
}
</style>
