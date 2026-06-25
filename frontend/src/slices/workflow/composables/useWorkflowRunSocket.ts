// WS subscriber for live workflow-run updates (§22.14).
//
// Subscribes to /ws/workflow-runs/{runId} and pushes events into:
//   - useWorkflowStore (liveSteps, runEvents)
//   - TanStack Query invalidation (steps, run, approvals)

import { useQueryClient } from '@tanstack/vue-query'
import { onActivated, onBeforeUnmount, onDeactivated, onMounted, ref } from 'vue'

import { wsManager, type ChannelEvent } from '@shared/transport'
import { useWorkflowStore } from '../stores/workflow'
import { wfKeys } from '../queries'
import { listSteps } from '../api'
import type { WorkflowRunEvent } from '../types'

export function useWorkflowRunSocket(runId: string) {
  const qc = useQueryClient()
  const wfStore = useWorkflowStore()
  const connected = ref(false)

  const path = `/workflow-runs/${runId}`
  const channel = wsManager.channel(path)

  // Monotonic generation guard: each reconnect bumps this counter, so a
  // slower in-flight sync started by an earlier reconnect cannot resolve last
  // and re-apply a stale snapshot over fresher data (R24.23).
  let syncGeneration = 0

  async function syncOnReconnect(): Promise<void> {
    const generation = ++syncGeneration
    try {
      const steps = await listSteps(runId)
      // A newer reconnect superseded this sync while it was in flight — drop it.
      if (generation !== syncGeneration) return
      // Clear stale events/steps before re-applying the authoritative snapshot
      // — without this, duplicate entries accumulate on every reconnect.
      wfStore.clearRunState()
      for (const s of steps) {
        wfStore.applyRunEvent({
          type: s.state === 'running' ? 'workflow.step_started'
            : s.state === 'failed' ? 'workflow.step_failed'
            : 'workflow.step_finished',
          node_id: s.node_id,
          state: s.state,
        } as unknown as WorkflowRunEvent)
      }
      qc.invalidateQueries({ queryKey: wfKeys.steps(runId) })
      qc.invalidateQueries({ queryKey: wfKeys.run(runId) })
    } catch {
      // Non-fatal — next WS event will catch up.
    }
  }

  function handleEvent(ev: ChannelEvent): void {
    const event = ev as unknown as WorkflowRunEvent
    wfStore.applyRunEvent(event)

    switch (event.type) {
      case 'workflow.step_started':
      case 'workflow.step_finished':
      case 'workflow.step_failed':
        // Any step transition can flip the run's own state (a resume out of
        // `waiting`, a step that fails the run, or the final step), so refresh
        // both the step list and the run header.
        qc.invalidateQueries({ queryKey: wfKeys.steps(runId) })
        qc.invalidateQueries({ queryKey: wfKeys.run(runId) })
        break
      case 'workflow.run_started':
      case 'workflow.run_finished':
      case 'workflow.run_cancelled':
        qc.invalidateQueries({ queryKey: wfKeys.run(runId) })
        break
      case 'approval.requested':
      case 'approval.resolved':
        qc.invalidateQueries({ queryKey: wfKeys.approvals(runId) })
        break
    }
  }

  const unsubscribeEvent = channel.subscribe('*', handleEvent)
  const unsubscribeStatus = channel.onStatus((isConnected) => {
    connected.value = isConnected
    if (isConnected) void syncOnReconnect()
  })

  onMounted(() => {
    wfStore.clearRunState()
    channel.connect()
  })

  onActivated(() => {
    channel.connect()
  })

  onDeactivated(() => {
    channel.disconnect()
  })

  onBeforeUnmount(() => {
    unsubscribeEvent()
    unsubscribeStatus()
    wsManager.close(path)
  })

  return { connected }
}
