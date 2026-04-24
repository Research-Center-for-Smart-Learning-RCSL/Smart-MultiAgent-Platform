<script setup lang="ts">
import { ImpersonationBanner } from '@slices/admin'
import { useBanKickGuard } from '@shared/composables'

useBanKickGuard()
</script>

<template>
  <ImpersonationBanner />
  <router-view />
</template>
