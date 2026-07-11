// Real-time Knowledge Map build progress (Phase 3β/4β, R11.24).
// Subscribes to /ws/knowmap/{id} for `build.state` events — same shared
// engine as useGraphragSocket (useBuildStateSocket), not useRagConfigSocket's
// multi-stage ingestion protocol (the Knowledge Map WS channel carries only
// build.state, same as Concept Map's, per ws/knowmap.py's docstring).
// Unlike GraphRAG there is no dedicated GET .../status endpoint; the backstop
// re-fetches the config itself and reads last_build_state off it.

import { useBuildStateSocket } from './useBuildStateSocket'
import { agentKeys } from '../queries'
import { agentsApi } from '../api'

export function useKnowmapSocket(projectId: string) {
  return useBuildStateSocket({
    pathPrefix: '/knowmap',
    fetchStatus: async (configId) => (await agentsApi.getKnowmapConfig(configId)).last_build_state,
    invalidateKeysOnTerminal: (configId) => [
      agentKeys.knowmapConfigs(projectId),
      agentKeys.knowmapConfig(configId),
    ],
  })
}
