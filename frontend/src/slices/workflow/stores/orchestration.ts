// Re-export from shared/stores/ — the orchestration store was promoted
// to shared because it is consumed by both the conversation and workflow
// slices (H15 cross-cutting concern).
export { useOrchestrationStore } from '@shared/stores/orchestration'
