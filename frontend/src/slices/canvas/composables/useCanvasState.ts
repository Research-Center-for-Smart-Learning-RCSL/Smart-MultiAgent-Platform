import { computed, type Ref } from 'vue'
import { useQuery, useQueryClient, useMutation } from '@tanstack/vue-query'
import { canvasKeys } from '../queries'
import * as canvasApi from '../api'
import type { Canvas } from '../types'

export function useCanvasState(chatroomId: Ref<string>) {
  const queryClient = useQueryClient()

  const canvasQuery = useQuery({
    queryKey: computed(() => canvasKeys.canvas(chatroomId.value)),
    queryFn: () => canvasApi.getCanvas(chatroomId.value),
    enabled: computed(() => !!chatroomId.value),
  })

  const canvas = computed<Canvas | undefined>(() => canvasQuery.data.value)
  const isLoading = computed(() => canvasQuery.isLoading.value)
  const error = computed(() => canvasQuery.error.value)

  const saveSnapshotMut = useMutation({
    mutationFn: (body?: { label?: string }) => canvasApi.createSnapshot(chatroomId.value, body),
  })

  const updateSettingsMut = useMutation({
    mutationFn: (body: { expose_to_agents: boolean }) =>
      canvasApi.updateCanvasSettings(chatroomId.value, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: canvasKeys.canvas(chatroomId.value) })
    },
  })

  return {
    canvas,
    isLoading,
    error,
    saveSnapshot: saveSnapshotMut.mutateAsync,
    updateSettings: updateSettingsMut.mutateAsync,
    isSavingSnapshot: saveSnapshotMut.isPending,
  }
}
