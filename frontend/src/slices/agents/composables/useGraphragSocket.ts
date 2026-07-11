// Real-time GraphRAG build progress (R11.04).
// Subscribes to /ws/graphrag/{id} for `build.state` events, replacing REST
// polling. The list view can watch several configs at once — shared engine
// (poll backstop, watch/unwatch, seed-state race guard) lives in
// useBuildStateSocket; this just supplies the GraphRAG-specific path,
// status fetch, and terminal-state invalidation.

import { useBuildStateSocket } from './useBuildStateSocket'
import { agentKeys } from '../queries'
import { agentsApi } from '../api'

export function useGraphragSocket(projectId: string) {
  return useBuildStateSocket({
    pathPrefix: '/graphrag',
    fetchStatus: async (configId) => (await agentsApi.getGraphragStatus(configId)).state,
    invalidateKeysOnTerminal: (configId) => [
      agentKeys.graphragConfigs(projectId),
      agentKeys.graphragConfig(configId),
      // The agent Knowledge tab's read-only coverage badge (WS5): this
      // composable only knows the config id, not which agent(s) it covers
      // (a chatroom/agent_group/workspace map can cover several), so it
      // can't target a single agentId — invalidating the whole prefix is
      // the only correct option and is cheap (TanStack refetches only the
      // currently-mounted queries under it).
      ['agents', 'conceptMapCoverage'],
    ],
  })
}
