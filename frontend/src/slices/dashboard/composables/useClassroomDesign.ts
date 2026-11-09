// The project's latest classroom design run ([R34.08]): polled while it is in
// flight, so a reload or a second tab picks the same run back up. The server
// keeps one run in flight per project and frees a stuck one by itself, so the
// client holds no job state of its own.

import { computed, ref } from 'vue'
import { useMutation, useQuery, useQueryClient } from '@tanstack/vue-query'
import type { DesignRunIn, DesignRunOut } from '@shared/api-client'
import { isProblemWithType } from '@shared/transport'
import { agentKeys } from '@slices/agents'
import { classroomDesignApi } from '../api/classroomDesign'
import { dashboardKeys } from '../queries'

const POLL_MS = 3000
const IN_FLIGHT = new Set<DesignRunOut['status']>(['pending', 'running'])

export type ApplyOutcome = 'applied' | 'conflict' | 'not-applicable' | 'failed'

export function useClassroomDesign(projectId: () => string) {
  const qc = useQueryClient()
  const key = computed(() => dashboardKeys.designRun(projectId()))

  const runQuery = useQuery({
    queryKey: key,
    enabled: computed(() => !!projectId()),
    queryFn: () => classroomDesignApi.latest(projectId()),
    refetchInterval: (query) => (query.state.data && IN_FLIGHT.has(query.state.data.status) ? POLL_MS : false),
  })
  const run = computed(() => runQuery.data.value ?? null)
  const inFlight = computed(() => !!run.value && IN_FLIGHT.has(run.value.status))
  // Set when an apply found an agent changed; cleared by the next run.
  const conflict = ref(false)

  const createMutation = useMutation({
    mutationFn: (body: DesignRunIn) => classroomDesignApi.create(projectId(), body),
    onSuccess: (created) => {
      conflict.value = false
      qc.setQueryData(key.value, created)
    },
    onSettled: () => qc.invalidateQueries({ queryKey: key.value }),
  })

  const applyMutation = useMutation({
    mutationFn: (runId: string) => classroomDesignApi.apply(projectId(), runId),
    onSuccess: (result) => {
      qc.setQueryData(key.value, result.run)
      for (const agentId of [result.run.teacher.agent_id, result.run.peer.agent_id]) {
        qc.invalidateQueries({ queryKey: agentKeys.agent(agentId) })
        qc.invalidateQueries({ queryKey: agentKeys.promptRevisions(agentId) })
      }
      qc.invalidateQueries({ queryKey: agentKeys.agents(projectId()) })
    },
  })

  async function apply(runId: string): Promise<ApplyOutcome> {
    try {
      await applyMutation.mutateAsync(runId)
      return 'applied'
    } catch (err) {
      if (isProblemWithType(err, '/agents/design-apply-conflict')) {
        conflict.value = true
        return 'conflict'
      }
      void qc.invalidateQueries({ queryKey: key.value })
      return isProblemWithType(err, '/agents/design-run-not-applicable') ? 'not-applicable' : 'failed'
    }
  }

  return {
    run,
    inFlight,
    conflict,
    isLoading: runQuery.isLoading,
    error: runQuery.error,
    refetch: runQuery.refetch,
    create: createMutation.mutateAsync,
    creating: createMutation.isPending,
    apply,
    applying: applyMutation.isPending,
  }
}
