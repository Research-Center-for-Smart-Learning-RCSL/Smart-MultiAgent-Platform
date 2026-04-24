<script setup lang="ts">
import { useI18n } from 'vue-i18n'

const { t } = useI18n()
</script>

<template>
  <main>
    <h1>{{ t('app.title') }}</h1>
    <p>{{ t('app.landingMessage') }}</p>
  </main>
</template>
