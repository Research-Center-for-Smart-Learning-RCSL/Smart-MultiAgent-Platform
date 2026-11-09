import { onScopeDispose, readonly, ref, type Ref } from 'vue'

/** Whether the page is currently visible (`document.visibilityState`). True
 *  outside a browser so server-side and test callers see the common case. */
export function usePageVisible(): Readonly<Ref<boolean>> {
  const visible = ref(typeof document === 'undefined' || document.visibilityState === 'visible')
  if (typeof document !== 'undefined') {
    const onChange = (): void => {
      visible.value = document.visibilityState === 'visible'
    }
    document.addEventListener('visibilitychange', onChange)
    onScopeDispose(() => document.removeEventListener('visibilitychange', onChange))
  }
  return readonly(visible)
}
