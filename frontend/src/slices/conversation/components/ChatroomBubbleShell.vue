<template>
  <article
    class="bubble"
    :class="[{ 'bubble--agent': agent, 'bubble--own': own && !agent }, colorClass]"
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
import { isAgentBubbleColor, type AgentBubbleColor } from '../utils/agentBubbleColors'

const props = defineProps<{
  agent?: boolean
  own?: boolean
  agentColor?: AgentBubbleColor | null
}>()

const colorClass = computed(() =>
  props.agent && isAgentBubbleColor(props.agentColor) ? `bubble--agent-${props.agentColor}` : undefined,
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


.bubble--agent-cyan {
  background: var(--color-agent-cyan-tint);
  border-left-color: var(--color-agent-cyan);
}

.bubble--agent-teal {
  background: var(--color-agent-teal-tint);
  border-left-color: var(--color-agent-teal);
}

.bubble--agent-green {
  background: var(--color-agent-green-tint);
  border-left-color: var(--color-agent-green);
}

.bubble--agent-lime {
  background: var(--color-agent-lime-tint);
  border-left-color: var(--color-agent-lime);
}

.bubble--agent-amber {
  background: var(--color-agent-amber-tint);
  border-left-color: var(--color-agent-amber);
}

.bubble--agent-orange {
  background: var(--color-agent-orange-tint);
  border-left-color: var(--color-agent-orange);
}

.bubble--agent-rose {
  background: var(--color-agent-rose-tint);
  border-left-color: var(--color-agent-rose);
}

.bubble--agent-violet {
  background: var(--color-agent-violet-tint);
  border-left-color: var(--color-agent-violet);
}

.bubble__meta {
  display: flex;
  align-items: center;
  gap: var(--space-2);
  margin-bottom: var(--space-1-5);
}
</style>
