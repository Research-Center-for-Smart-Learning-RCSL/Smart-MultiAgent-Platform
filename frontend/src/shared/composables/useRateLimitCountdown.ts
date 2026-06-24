import { ref, onUnmounted } from 'vue'

export interface RateLimitCountdown {
  seconds: ReturnType<typeof ref<number>>
  active: ReturnType<typeof ref<boolean>>
  start: (durationSeconds: number) => void
  stop: () => void
}

export function useRateLimitCountdown(): RateLimitCountdown {
  const seconds = ref(0)
  const active = ref(false)
  let timer: ReturnType<typeof setInterval> | undefined

  function stop(): void {
    clearInterval(timer)
    timer = undefined
    seconds.value = 0
    active.value = false
  }

  function start(durationSeconds: number): void {
    stop()
    seconds.value = durationSeconds
    active.value = true
    timer = setInterval(() => {
      seconds.value--
      if (seconds.value <= 0) stop()
    }, 1000)
  }

  onUnmounted(stop)

  return { seconds, active, start, stop }
}
