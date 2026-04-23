// WS subscriber for live workflow-run updates (§22.14).
//
// Subscribes to /ws/workflow-runs/{runId} and pushes events into:
//   - useWorkflowStore (liveSteps, runEvents)
//   - TanStack Query invalidation (steps, run, approvals)

import { useQueryClient } from '@tanstack/vue-query'
import { onBeforeUnmount, onMounted, ref } from 'vue'

import { getAccessToken } from '@shared/transport'
import { useWorkflowStore } from '../stores/workflow'
import { wfKeys } from '../queries'
import type { WorkflowRunEvent } from '../types'

const RECONNECT_DELAY_MS = 1500

export function useWorkflowRunSocket(runId: string) {
  const qc = useQueryClient()
  const wfStore = useWorkflowStore()
  const connected = ref(false)
  let socket: WebSocket | null = null
  let reconnectTimer: number | null = null

  function wsUrl(): string {
    const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
    return `${proto}://${window.location.host}/ws/workflow-runs/${runId}`
  }

  function subprotocols(): string[] {
    const token = getAccessToken()
    if (!token) return []
    return [`bearer.${token}`]
  }

  function handleEvent(event: WorkflowRunEvent): void {
    wfStore.applyRunEvent(event)

    switch (event.type) {
      case 'workflow.step_started':
      case 'workflow.step_finished':
      case 'workflow.step_failed':
        qc.invalidateQueries({ queryKey: wfKeys.steps(runId) })
        break
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

  function connect(): void {
    if (socket) return
    try {
      socket = new WebSocket(wsUrl(), subprotocols())
    } catch {
      scheduleReconnect()
      return
    }

    socket.onopen = () => {
      connected.value = true
    }

    socket.onclose = () => {
      connected.value = false
      socket = null
      scheduleReconnect()
    }

    socket.onerror = () => {
      socket?.close()
    }

    socket.onmessage = (msg) => {
      try {
        const event = JSON.parse(msg.data) as WorkflowRunEvent
        handleEvent(event)
      } catch {
        // Malformed message — skip.
      }
    }
  }

  function scheduleReconnect(): void {
    if (reconnectTimer != null) return
    reconnectTimer = window.setTimeout(() => {
      reconnectTimer = null
      connect()
    }, RECONNECT_DELAY_MS)
  }

  function disconnect(): void {
    if (reconnectTimer != null) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (socket) {
      socket.onclose = null
      socket.close()
      socket = null
    }
    connected.value = false
  }

  onMounted(() => {
    wfStore.clearRunState()
    connect()
  })

  onBeforeUnmount(() => {
    disconnect()
  })

  return { connected }
}
