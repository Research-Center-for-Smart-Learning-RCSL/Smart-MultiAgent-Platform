import { ref } from 'vue'
import { useMutation } from '@tanstack/vue-query'
import { useI18n } from 'vue-i18n'
import { useToast } from '@shared/composables'
import { agentsApi } from '../api'

export type ToolTestKind = 'mcp' | 'function'

export interface ToolTestFailure {
  error: string
  duration_ms: number
}

interface ToolTestVars {
  toolId: string
  kind: ToolTestKind
}

export function useToolTest(agentId: string) {
  const { t } = useI18n()
  const toast = useToast()
  const testingIds = ref<Set<string>>(new Set())
  // MCP failures open a detail modal (server discovery errors are long); function
  // failures (egress allowlist, transport) are concise enough for a toast.
  const failedResult = ref<ToolTestFailure | null>(null)

  const isTesting = (toolId: string): boolean => testingIds.value.has(toolId)

  const testMutation = useMutation({
    mutationFn: (vars: ToolTestVars) => agentsApi.testTool(agentId, vars.toolId),
    onSuccess: (res, vars) => {
      if (res.data.ok) {
        if (vars.kind === 'function') {
          toast.success(
            t('agents.tools.functions.testOk', {
              status: res.data.status ?? '',
              ms: res.data.duration_ms,
            }),
          )
        } else {
          toast.success(
            t('agents.tools.mcp.testOk', {
              count: res.data.tool_names.length,
              ms: res.data.duration_ms,
            }),
          )
        }
      } else if (vars.kind === 'function') {
        toast.error(res.data.error ?? t('agents.tools.functions.testFailed'))
      } else {
        failedResult.value = {
          error: res.data.error ?? t('agents.tools.mcp.testBad'),
          duration_ms: res.data.duration_ms,
        }
      }
    },
    onError: (_err, vars) =>
      toast.error(
        t(
          vars.kind === 'function'
            ? 'agents.tools.functions.testFailed'
            : 'agents.tools.mcp.testFailed',
        ),
      ),
    onSettled: (_res, _err, vars) => {
      const next = new Set(testingIds.value)
      next.delete(vars.toolId)
      testingIds.value = next
    },
  })

  function runTest(toolId: string, kind: ToolTestKind = 'mcp'): void {
    testingIds.value = new Set(testingIds.value).add(toolId)
    testMutation.mutate({ toolId, kind })
  }

  return { testingIds, isTesting, runTest, failedResult }
}
