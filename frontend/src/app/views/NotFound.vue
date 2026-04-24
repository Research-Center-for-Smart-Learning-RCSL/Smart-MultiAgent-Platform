<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
</script>

<template>
  <section class="not-found flex flex-col items-center justify-center min-h-[60vh]">
    <h1 class="text-4xl font-bold mb-4">
      404
    </h1>
    <p class="text-gray-600 mb-6">
      {{ t('app.notFound') }}
    </p>
    <router-link
      to="/"
      class="text-blue-600 hover:underline"
    >
      {{ t('app.backToHome') }}
    </router-link>
  </section>
</template>
