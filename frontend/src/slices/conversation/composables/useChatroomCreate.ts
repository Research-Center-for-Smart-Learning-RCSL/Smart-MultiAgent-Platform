import { reactive, ref, toValue, type MaybeRefOrGetter } from 'vue'
import { useRouter } from 'vue-router'
import { useMutation, useQueryClient } from '@tanstack/vue-query'
import { useI18n } from 'vue-i18n'
import { useToast } from '@shared/composables'
import { createChatroom } from '../api'
import { convKeys } from '../queries'
import { chatroomCreateSchema, type ChatroomCreateInput } from '../types/schemas'

export function useChatroomCreate(workspaceId: MaybeRefOrGetter<string | null>) {
  const { t } = useI18n()
  const router = useRouter()
  const qc = useQueryClient()
  const toast = useToast()

  const showCreate = ref(false)
  const createName = ref('')
  const createError = ref<string | null>(null)
  const createFlags = reactive({
    allow_org_members: false,
    allow_project_members: true,
    allow_project_owners_only: false,
    allow_guest_links: false,
  })

  function openCreate(): void {
    createName.value = ''
    createError.value = null
    createFlags.allow_org_members = false
    createFlags.allow_project_members = true
    createFlags.allow_project_owners_only = false
    createFlags.allow_guest_links = false
    showCreate.value = true
  }

  const createMutation = useMutation({
    mutationFn: (payload: ChatroomCreateInput) => {
      const wsId = toValue(workspaceId)
      if (!wsId) throw new Error('No workspace selected')
      return createChatroom(wsId, payload)
    },
    onSuccess: (room) => {
      showCreate.value = false
      qc.invalidateQueries({ queryKey: convKeys.chatroomsAll() })
      toast.success(t('conversation.chatrooms.created'))
      router.push({ name: 'conversation.chatroom', params: { chatroomId: room.id } })
    },
    onError: () => toast.error(t('conversation.chatrooms.createFailed')),
  })

  function submitCreate(): void {
    const parsed = chatroomCreateSchema.safeParse({ name: createName.value, ...createFlags })
    if (!parsed.success) {
      createError.value = t('conversation.chatrooms.nameInvalid')
      return
    }
    createError.value = null
    createMutation.mutate(parsed.data)
  }

  return {
    showCreate,
    createName,
    createError,
    createFlags,
    openCreate,
    submitCreate,
    isCreating: createMutation.isPending,
  }
}
