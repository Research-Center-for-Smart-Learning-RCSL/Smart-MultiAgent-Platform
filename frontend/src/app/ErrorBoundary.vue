<script setup lang="ts">
// Global render-error boundary (FE-11).
//
// `app.config.errorHandler` shows a toast but Vue still tears the failed
// component subtree down — so a single null-access bug in any view degrades
// the whole app to a blank screen. `onErrorCaptured` lets us swap in a
// fallback for that subtree instead, keeping the rest of the shell alive.

import { onErrorCaptured, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { AuthError } from '@shared/errors'
import { reportError } from './errorHandler'

const failed = ref(false)
const route = useRoute()

onErrorCaptured((err) => {
  // Auth failures must reach the global handler (which redirects to login),
  // so let those keep propagating. Every other throw is contained here.
  if (err instanceof AuthError) return true
  failed.value = true
  if (import.meta.env.PROD) {
    reportError(err)
  } else {
    console.error(err)
  }
  // Stop propagation: the fallback below is the user-facing feedback, so a
  // duplicate global toast would be noise.
  return false
})

// Clear the fallback on navigation. The boundary outlives the keyed
// <router-view>, so without this a crash on one route would pin every
// subsequent route to the error screen.
watch(
  () => route.fullPath,
  () => {
    failed.value = false
  },
)

function retry(): void {
  // Clear the fallback so the keyed <router-view> re-renders. Transient
  // loading-race throws recover; a hard bug simply re-trips the boundary.
  failed.value = false
}
</script>

<template>
  <div
    v-if="failed"
    class="error-boundary"
    role="alert"
  >
    <h1>{{ $t('app.errorBoundary.title') }}</h1>
    <p>{{ $t('app.errorBoundary.message') }}</p>
    <button
      type="button"
      @click="retry"
    >
      {{ $t('app.errorBoundary.retry') }}
    </button>
  </div>
  <slot v-else />
</template>

<style scoped>
.error-boundary {
  max-width: 32rem;
  margin: 4rem auto;
  padding: 1.5rem;
  text-align: center;
}
</style>
