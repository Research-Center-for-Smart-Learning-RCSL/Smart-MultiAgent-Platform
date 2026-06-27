import { ref } from 'vue'
import { useMutation } from '@tanstack/vue-query'
import { useI18n } from 'vue-i18n'
import { useToast } from '@shared/composables'
import { agentsApi } from '../api'

export interface ToolTestFailure {
  error: string
  duration_ms: number
}

export function useToolTest(agentId: string) {
  const { t } = useI18n()
  const toast = useToast()
  const testingIds = ref<Set<string>>(new Set())
  const failedResult = ref<ToolTestFailure | null>(null)

  const isTesting = (toolId: string): boolean => testingIds.value.has(toolId)

  const testMutation = useMutation({
    mutationFn: (toolId: string) => agentsApi.testTool(agentId, toolId),
    onSuccess: (res) => {
      if (res.data.ok) {
        toast.success(
          t('agents.tools.mcp.testOk', {
            count: res.data.tool_names.length,
            ms: res.data.duration_ms,
          }),
        )
      } else {
        failedResult.value = {
          error: res.data.error ?? t('agents.tools.mcp.testBad'),
          duration_ms: res.data.duration_ms,
        }
      }
    },
    onError: () => toast.error(t('agents.tools.mcp.testFailed')),
    onSettled: (_res, _err, toolId) => {
      const next = new Set(testingIds.value)
      next.delete(toolId)
      testingIds.value = next
    },
  })

  function runTest(toolId: string): void {
    testingIds.value = new Set(testingIds.value).add(toolId)
    testMutation.mutate(toolId)
  }

  return { testingIds, isTesting, runTest, failedResult }
}
