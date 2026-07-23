// Pinia store for orchestration ephemeral state (G.10).
//
// Approval inline cards are driven by WS events (approval.requested,
// approval.resolved) published on the chatroom channel. This store
// tracks live approval gates per room so the UI can render them.
//
// Moved to shared/stores/ because both conversation and workflow slices
// consume it (H15 cross-cutting concern).

import { defineStore } from 'pinia'
import { reactive, ref } from 'vue'
import { registerCleanup } from '@shared/stores/useAppCleanup'
import type { ApprovalWithVotes } from '@shared/types/workflow'

export const useOrchestrationStore = defineStore('orchestration', () => {
  // Map<roomId, Map<approvalId, approval>>
  const liveApprovals = reactive<Record<string, Record<string, ApprovalWithVotes>>>({})
  const expandedChainId = ref<string | null>(null)

  function upsertApproval(roomId: string, approval: ApprovalWithVotes): void {
    if (!liveApprovals[roomId]) {
      liveApprovals[roomId] = {}
    }
    liveApprovals[roomId][approval.id] = approval
  }

  function resolveApproval(
    roomId: string,
    approvalId: string,
    state: string,
  ): void {
    const map = liveApprovals[roomId]
    if (!map?.[approvalId]) return
    map[approvalId] = { ...map[approvalId], state: state as ApprovalWithVotes['state'] }
  }

  function removeApproval(roomId: string, approvalId: string): void {
    const map = liveApprovals[roomId]
    if (!map) return
    delete map[approvalId]
  }

  // Reconcile pinned pending cards against the server. Cards are inserted from
  // WS alone (approval.requested), so a dropped approval.resolved — or a gate
  // whose creation rolled back before its row was durable (F-18) — would pin a
  // `pending` card until reload. For each pending card past its own
  // timeout_seconds grace, fetch the authoritative state: a resolved state
  // transitions the card, an absent (null) approval removes it. `fetchApproval`
  // is injected so this shared store never imports the workflow slice; the
  // caller maps a 404 to null. `now` is injectable for deterministic tests.
  async function reconcilePending(
    roomId: string,
    fetchApproval: (approvalId: string) => Promise<ApprovalWithVotes | null>,
    opts: { now?: number } = {},
  ): Promise<void> {
    const now = opts.now ?? Date.now()
    const map = liveApprovals[roomId]
    if (!map) return
    const pending = Object.values(map).filter((a) => a.state === 'pending')
    for (const a of pending) {
      const startedMs = Date.parse(a.started_at)
      const graceMs = (a.timeout_seconds ?? 300) * 1000
      // A fresh card within its grace window is expected to be pending — leave
      // it alone so reconciliation never races a just-created gate.
      if (Number.isFinite(startedMs) && now < startedMs + graceMs) continue
      // A card removed/resolved concurrently (e.g. by a WS event that arrived
      // mid-loop) is no longer our concern.
      if (!map[a.id]) continue
      let server: ApprovalWithVotes | null
      try {
        server = await fetchApproval(a.id)
      } catch {
        // Transient/unexpected error — keep the card and try again next pass.
        continue
      }
      if (server === null) {
        removeApproval(roomId, a.id)
      } else if (server.state !== 'pending') {
        resolveApproval(roomId, a.id, server.state)
      }
    }
  }

  function getApprovalsForRoom(roomId: string): ApprovalWithVotes[] {
    return Object.values(liveApprovals[roomId] ?? {})
  }

  function setExpandedChain(chainId: string | null): void {
    expandedChainId.value = chainId
  }

  function clearAll(): void {
    Object.keys(liveApprovals).forEach((k) => delete liveApprovals[k])
    expandedChainId.value = null
  }

  // Register with the shared cleanup registry so session.clear() can
  // reset orchestration state without importing this store directly (H14).
  registerCleanup(clearAll)

  return {
    liveApprovals,
    expandedChainId,
    upsertApproval,
    resolveApproval,
    removeApproval,
    reconcilePending,
    getApprovalsForRoom,
    setExpandedChain,
    clearAll,
  }
})
