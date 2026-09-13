<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import SButton from '@shared/ui/SButton.vue'

const { t } = useI18n()

const model = defineModel<string>({ required: true })

const windows = ['1h', '6h', '24h'] as const
</script>

<template>
  <div class="flex gap-2">
    <SButton
      v-for="w in windows"
      :key="w"
      size="sm"
      :variant="model === w ? 'primary' : 'ghost'"
      @click="model = w"
    >
      {{ t(`dashboard.timeWindow.${w}`) }}
    </SButton>
  </div>
</template>
