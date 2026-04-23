<script setup lang="ts">
// Inline approval card — renders in the chatroom message list when an
// approval.requested WS event arrives. Shows approver votes in real-time
// and the leader marker. Timeout countdown driven by started_at + timeout_seconds.

import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import type { ApprovalWithVotes } from '../types'

const props = defineProps<{
  approval: ApprovalWithVotes
  agentNames: Record<string, string>
}>()

const now = ref(Date.now())
let ticker: number | null = null

onMounted(() => {
  ticker = window.setInterval(() => { now.value = Date.now() }, 1000)
})
onBeforeUnmount(() => {
  if (ticker !== null) clearInterval(ticker)
})

const isPending = computed(() => props.approval.state === 'pending')

const deadline = computed(() => {
  const start = new Date(props.approval.started_at).getTime()
  return start + props.approval.timeout_seconds * 1000
})

const remainingSeconds = computed(() => {
  if (!isPending.value) return 0
  return Math.max(0, Math.ceil((deadline.value - now.value) / 1000))
})

const remainingFormatted = computed(() => {
  const s = remainingSeconds.value
  const m = Math.floor(s / 60)
  const sec = s % 60
  return `${m}:${String(sec).padStart(2, '0')}`
})

const stateLabel = computed(() => {
  const map: Record<string, string> = {
    pending: 'Pending',
    approved: 'Approved',
    rejected: 'Rejected',
    timeout_leader: 'Timeout (Leader)',
  }
  return map[props.approval.state] ?? props.approval.state
})

const stateClass = computed(() => {
  return {
    pending: 'text-yellow-600',
    approved: 'text-green-600',
    rejected: 'text-red-600',
    timeout_leader: 'text-orange-600',
  }[props.approval.state] ?? ''
})

function agentName(id: string): string {
  return props.agentNames[id] ?? id.slice(0, 8)
}

function voteForAgent(agentId: string) {
  return props.approval.votes.find((v) => v.voter_agent_id === agentId)
}
</script>

<template>
  <div class="approval-card rounded border p-3 my-2 bg-gray-50">
    <div class="flex items-center justify-between mb-2">
      <span class="font-semibold text-sm">
        Approval Gate
        <span class="text-xs text-gray-500 ml-1">({{ approval.mode }})</span>
      </span>
      <span :class="stateClass" class="text-sm font-medium">
        {{ stateLabel }}
      </span>
    </div>

    <div v-if="isPending" class="text-xs text-gray-500 mb-2">
      Timeout in {{ remainingFormatted }}
    </div>

    <ul class="space-y-1">
      <li
        v-for="aid in approval.approver_agent_ids"
        :key="aid"
        class="flex items-center gap-2 text-sm"
      >
        <span
          v-if="aid === approval.leader_agent_id"
          class="text-xs bg-blue-100 text-blue-700 px-1 rounded"
        >
          Leader
        </span>
        <span class="font-mono text-xs">{{ agentName(aid) }}</span>
        <template v-if="voteForAgent(aid)">
          <span
            :class="voteForAgent(aid)!.vote ? 'text-green-600' : 'text-red-600'"
            class="text-xs font-medium"
          >
            {{ voteForAgent(aid)!.vote ? 'Approve' : 'Reject' }}
          </span>
          <span
            v-if="voteForAgent(aid)!.rationale"
            class="text-xs text-gray-400 truncate max-w-48"
          >
            {{ voteForAgent(aid)!.rationale }}
          </span>
        </template>
        <span v-else class="text-xs text-gray-400">Pending</span>
      </li>
    </ul>
  </div>
</template>
