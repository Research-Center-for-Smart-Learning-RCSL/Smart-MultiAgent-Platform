<template>
  <article
    class="bubble"
    :class="{ 'bubble--agent': agent, 'bubble--own': own && !agent, 'bubble--agent-colored': color }"
    :data-agent-color="color ?? undefined"
    :style="color ? agentColorVars(color) : undefined"
  >
    <header class="bubble__meta">
      <slot name="meta" />
    </header>
    <slot />
  </article>
</template>

<script setup lang="ts">
// Shared message-bubble frame for ChatroomMessageBubble + ChatroomStreamingBubble
// so the bubble surface/border/meta layout lives in one place. Slotted content
// keeps the caller's scope, so per-variant styling stays in each component.
import { computed } from 'vue'
import { agentColorVars, isAgentBubbleColor, type AgentBubbleColor } from '../utils/agentBubbleColors'

const props = defineProps<{
  agent?: boolean
  own?: boolean
  agentColor?: AgentBubbleColor | null
}>()

const color = computed<AgentBubbleColor | null>(() =>
  props.agent && isAgentBubbleColor(props.agentColor) ? props.agentColor : null,
)
</script>

<style scoped>
.bubble {
  background: var(--color-bg);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-md);
  padding: var(--space-3) var(--space-4);
  max-width: 75%;
  min-width: 200px;
}

.bubble--own {
  background: var(--color-bubble-own);
}

.bubble--agent {
  background: var(--color-surface);
  border-left: 3px solid var(--color-accent);
  max-width: 85%;
}

/* The inline style from agentColorVars() overrides these; they keep a missing
   binding looking like an uncoloured agent rather than transparent. */
.bubble--agent-colored {
  --agent-color: var(--color-accent);
  --agent-color-tint: var(--color-surface);
  background: var(--agent-color-tint);
  border-left-color: var(--agent-color);
}

.bubble__meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-1-5);
}
</style>
