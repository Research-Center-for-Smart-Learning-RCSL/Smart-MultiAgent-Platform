import { computed, type Ref } from 'vue'
import { useQuery, useQueryClient, useMutation } from '@tanstack/vue-query'
import { canvasKeys } from '../queries'
import * as canvasApi from '../api'
import type { CanvasComment } from '../types'

export function useCanvasComments(chatroomId: Ref<string>, objectId: Ref<string | null>) {
  const queryClient = useQueryClient()

  const commentsQuery = useQuery({
    queryKey: computed(() =>
      canvasKeys.comments(chatroomId.value, objectId.value ?? ''),
    ),
    queryFn: () => canvasApi.listComments(chatroomId.value, objectId.value!),
    enabled: computed(() => !!chatroomId.value && !!objectId.value),
  })

  const commentCountsQuery = useQuery({
    queryKey: computed(() => canvasKeys.commentCounts(chatroomId.value)),
    queryFn: () => canvasApi.getCommentCounts(chatroomId.value),
    enabled: computed(() => !!chatroomId.value),
  })

  const comments = computed<CanvasComment[]>(() => commentsQuery.data.value ?? [])
  const commentCounts = computed<Record<string, number>>(
    () => commentCountsQuery.data.value ?? {},
  )

  const createMut = useMutation({
    mutationFn: (content: string) =>
      canvasApi.createComment(chatroomId.value, objectId.value!, { content }),
    onSuccess: () => {
      if (objectId.value) {
        queryClient.invalidateQueries({
          queryKey: canvasKeys.comments(chatroomId.value, objectId.value),
        })
      }
      queryClient.invalidateQueries({
        queryKey: canvasKeys.commentCounts(chatroomId.value),
      })
    },
  })

  const updateMut = useMutation({
    mutationFn: ({ commentId, content }: { commentId: string; content: string }) =>
      canvasApi.updateComment(chatroomId.value, commentId, { content }),
    onSuccess: () => {
      if (objectId.value) {
        queryClient.invalidateQueries({
          queryKey: canvasKeys.comments(chatroomId.value, objectId.value),
        })
      }
    },
  })

  const deleteMut = useMutation({
    mutationFn: (commentId: string) =>
      canvasApi.deleteComment(chatroomId.value, commentId),
    onSuccess: () => {
      if (objectId.value) {
        queryClient.invalidateQueries({
          queryKey: canvasKeys.comments(chatroomId.value, objectId.value),
        })
      }
      queryClient.invalidateQueries({
        queryKey: canvasKeys.commentCounts(chatroomId.value),
      })
    },
  })

  return {
    comments,
    commentCounts,
    isLoading: commentsQuery.isLoading,
    createComment: createMut.mutateAsync,
    updateComment: updateMut.mutateAsync,
    deleteComment: deleteMut.mutateAsync,
    isCreating: createMut.isPending,
  }
}
