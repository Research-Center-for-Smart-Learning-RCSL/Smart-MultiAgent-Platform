import { computed, type Ref } from 'vue'
import { useQuery, useQueryClient, useMutation } from '@tanstack/vue-query'
import { canvasKeys } from '../queries'
import * as canvasApi from '../api'
import type { Canvas, CanvasObject, CanvasObjectCreate, CanvasObjectPatch } from '../types'

export function useCanvasState(chatroomId: Ref<string>) {
  const queryClient = useQueryClient()

  const canvasQuery = useQuery({
    queryKey: computed(() => canvasKeys.canvas(chatroomId.value)),
    queryFn: () => canvasApi.getCanvas(chatroomId.value),
    enabled: computed(() => !!chatroomId.value),
  })

  const objectsQuery = useQuery({
    queryKey: computed(() => canvasKeys.objects(chatroomId.value)),
    queryFn: () => canvasApi.listObjects(chatroomId.value),
    enabled: computed(() => !!chatroomId.value && !!canvasQuery.data.value),
  })

  const canvas = computed<Canvas | undefined>(() => canvasQuery.data.value)
  const objects = computed<CanvasObject[]>(() => objectsQuery.data.value ?? [])
  const isLoading = computed(() => canvasQuery.isLoading.value || objectsQuery.isLoading.value)
  const error = computed(() => canvasQuery.error.value || objectsQuery.error.value)

  const createObjectMut = useMutation({
    mutationFn: (body: CanvasObjectCreate) => canvasApi.createObject(chatroomId.value, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: canvasKeys.objects(chatroomId.value) })
    },
  })

  const updateObjectMut = useMutation({
    mutationFn: ({ objectId, body }: { objectId: string; body: CanvasObjectPatch }) =>
      canvasApi.updateObject(chatroomId.value, objectId, body),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: canvasKeys.objects(chatroomId.value) })
    },
  })

  const deleteObjectMut = useMutation({
    mutationFn: (objectId: string) => canvasApi.deleteObject(chatroomId.value, objectId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: canvasKeys.objects(chatroomId.value) })
    },
  })

  const uploadImageMut = useMutation({
    mutationFn: (file: File) => canvasApi.uploadImage(chatroomId.value, file),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: canvasKeys.objects(chatroomId.value) })
    },
  })

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

  function invalidateAll() {
    queryClient.invalidateQueries({ queryKey: canvasKeys.canvas(chatroomId.value) })
    queryClient.invalidateQueries({ queryKey: canvasKeys.objects(chatroomId.value) })
  }

  return {
    canvas,
    objects,
    isLoading,
    error,
    createObject: createObjectMut.mutateAsync,
    updateObject: updateObjectMut.mutateAsync,
    deleteObject: deleteObjectMut.mutateAsync,
    uploadImage: uploadImageMut.mutateAsync,
    saveSnapshot: saveSnapshotMut.mutateAsync,
    updateSettings: updateSettingsMut.mutateAsync,
    isSavingSnapshot: saveSnapshotMut.isPending,
    invalidateAll,
  }
}
