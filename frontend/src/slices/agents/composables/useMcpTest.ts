import { ref } from 'vue'
import { useMutation } from '@tanstack/vue-query'
import { useI18n } from 'vue-i18n'
import { useToast } from '@shared/composables'
import { agentsApi } from '../api'

export function useMcpTest(agentId: string) {
  const { t } = useI18n()
  const toast = useToast()
  const testingIds = ref<Set<string>>(new Set())

  const isTesting = (bindingId: string): boolean => testingIds.value.has(bindingId)

  const testMutation = useMutation({
    mutationFn: (bindingId: string) => agentsApi.testMcpBinding(agentId, bindingId),
    onSuccess: (res) => {
      if (res.data.ok) {
        toast.success(
          t('agents.mcp.testOk', {
            count: res.data.tool_names.length,
            ms: res.data.duration_ms,
          }),
        )
      } else {
        toast.error(t('agents.mcp.testBad'))
      }
    },
    onError: () => toast.error(t('agents.mcp.testFailed')),
    onSettled: (_res, _err, bindingId) => {
      const next = new Set(testingIds.value)
      next.delete(bindingId)
      testingIds.value = next
    },
  })

  function runTest(bindingId: string): void {
    testingIds.value = new Set(testingIds.value).add(bindingId)
    testMutation.mutate(bindingId)
  }

  return { testingIds, isTesting, runTest }
}
