/**
 * Playwright globalSetup — provisions test entities via API so E2E specs
 * can run without manual E2E_* env vars.
 *
 * Creates: org, project, agent, workspace, chatroom, workflow.
 * Writes IDs to .e2e-seed.json (read by fixtures/seed.ts).
 */
import { request } from '@playwright/test'
import { writeFileSync } from 'fs'
import { resolve } from 'path'

const BASE = process.env.E2E_API_BASE ?? 'http://localhost:28000'
const SEED_FILE = resolve(__dirname, '.e2e-seed.json')

const ADMIN = { email: 'e2e-admin@example.com', password: 'E2eAdm1n!Str0ng' }
const USER = { email: 'e2e-user@example.com', password: 'E2eP@ssw0rd!Str0ng' }

async function globalSetup(): Promise<void> {
  const api = await request.newContext({ baseURL: BASE })

  // Login as admin
  const adminLogin = await api.post('/api/auth/login', { data: ADMIN })
  if (!adminLogin.ok()) {
    console.warn('[e2e-seed] Admin login failed — skipping seed (tests with env gates will be skipped)')
    writeFileSync(SEED_FILE, '{}', 'utf-8')
    await api.dispose()
    return
  }
  const adminTokens = await adminLogin.json()
  const adminAuth = { Authorization: `Bearer ${adminTokens.access_token}` }

  // Login as regular user
  const userLogin = await api.post('/api/auth/login', { data: USER })
  if (!userLogin.ok()) {
    console.warn('[e2e-seed] User login failed')
    writeFileSync(SEED_FILE, '{}', 'utf-8')
    await api.dispose()
    return
  }
  const userTokens = await userLogin.json()
  const userAuth = { Authorization: `Bearer ${userTokens.access_token}` }

  const seed: Record<string, string> = {}

  try {
    // Get admin user ID (for impersonation target)
    const meResp = await api.get('/api/auth/me', { headers: userAuth })
    if (meResp.ok()) {
      const me = await meResp.json()
      seed.E2E_TARGET_USER_ID = me.id
    }

    // Create org
    const orgResp = await api.post('/api/orgs', {
      headers: userAuth,
      data: { name: `e2e-org-${Date.now()}` },
    })
    if (orgResp.ok()) {
      const org = await orgResp.json()
      seed.E2E_ORG_ID = org.id

      // Create project in org
      const projResp = await api.post(`/api/orgs/${org.id}/projects`, {
        headers: userAuth,
        data: { name: `e2e-proj-${Date.now()}` },
      })
      if (projResp.ok()) {
        const proj = await projResp.json()
        seed.E2E_PROJECT_ID = proj.id

        // Create agent in project
        const agentResp = await api.post(`/api/projects/${proj.id}/agents`, {
          headers: userAuth,
          data: {
            name: `e2e-agent-${Date.now()}`,
            system_prompt: 'You are a test agent.',
            provider: 'openai',
            model_id: 'gpt-4o-mini',
          },
        })
        if (agentResp.ok()) {
          const agent = await agentResp.json()
          seed.E2E_AGENT_ID = agent.id
        }
      }
    }

    // Get or create workspace
    const wsListResp = await api.get('/api/workspaces', { headers: userAuth })
    if (wsListResp.ok()) {
      const workspaces = await wsListResp.json()
      let ws = workspaces[0]
      if (!ws) {
        const wsResp = await api.post('/api/workspaces', {
          headers: userAuth,
          data: { name: `e2e-workspace-${Date.now()}` },
        })
        if (wsResp.ok()) ws = await wsResp.json()
      }
      if (ws) {
        seed.E2E_WORKSPACE_ID = ws.id

        // Create chatroom in workspace
        const crResp = await api.post(`/api/workspaces/${ws.id}/chatrooms`, {
          headers: userAuth,
          data: { name: `e2e-chatroom-${Date.now()}` },
        })
        if (crResp.ok()) {
          const cr = await crResp.json()
          seed.E2E_CHATROOM_ID = cr.id
        }

        // Create workflow in workspace
        const wfResp = await api.post(`/api/workspaces/${ws.id}/workflows`, {
          headers: userAuth,
          data: {
            name: `e2e-workflow-${Date.now()}`,
            definition: {
              schema_version: 1,
              name: 'E2E Test Workflow',
              variables: {},
              timeouts: { run_max_seconds: 60, idle_max_seconds: 30 },
              entry_node_id: 'trigger_1',
              nodes: [
                { id: 'trigger_1', type: 'trigger', config: { trigger_type: 'manual' }, label: 'Start', position: { x: 0, y: 0 } },
                { id: 'end_1', type: 'end', config: {}, label: 'End', position: { x: 200, y: 0 } },
              ],
              edges: [
                { id: 'e1', from: 'trigger_1', to: 'end_1', from_port: 'default' },
              ],
            },
          },
        })
        if (wfResp.ok()) {
          const wf = await wfResp.json()
          seed.E2E_WORKFLOW_ID = wf.id
        }
      }
    }

    // Mark invite target (second user email)
    seed.E2E_INVITE_TARGET = ADMIN.email

    // Key group URL (create if project exists)
    if (seed.E2E_PROJECT_ID) {
      const kgResp = await api.post(`/api/projects/${seed.E2E_PROJECT_ID}/key-groups`, {
        headers: userAuth,
        data: { name: `e2e-keygroup-${Date.now()}` },
      })
      if (kgResp.ok()) {
        const kg = await kgResp.json()
        seed.E2E_KEY_GROUP_URL = `projects/${seed.E2E_PROJECT_ID}/key-groups/${kg.id}`
      }
    }
  } catch (err) {
    console.warn('[e2e-seed] Seeding error (tests with env gates will skip):', err)
  }

  const count = Object.keys(seed).length
  console.log(`[e2e-seed] Seeded ${count} entities:`, Object.keys(seed).join(', '))
  writeFileSync(SEED_FILE, JSON.stringify(seed, null, 2), 'utf-8')
  await api.dispose()
}

export default globalSetup
