// Pinia store for orchestration ephemeral state (G.10).
//
// Approval inline cards are driven by WS events (approval.requested,
// approval.resolved) published on the chatroom channel. This store
// tracks live approval gates per room so the UI can render them.

import { defineStore } from 'pinia'
import { reactive, ref } from 'vue'
import type { ApprovalWithVotes } from '../types'

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

  function getApprovalsForRoom(roomId: string): ApprovalWithVotes[] {
    return Object.values(liveApprovals[roomId] ?? {})
  }

  function setExpandedChain(chainId: string | null): void {
    expandedChainId.value = chainId
  }

  return {
    liveApprovals,
    expandedChainId,
    upsertApproval,
    resolveApproval,
    removeApproval,
    getApprovalsForRoom,
    setExpandedChain,
  }
})
