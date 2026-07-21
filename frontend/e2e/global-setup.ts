/**
 * Playwright globalSetup — provisions test entities via API so E2E specs
 * can run without manual E2E_* env vars.
 *
 * Creates: org, project, agent, workspace, chatroom, workflow.
 * Writes IDs to .e2e-seed.json (read by fixtures/seed.ts).
 */
import { request } from '@playwright/test'
import { writeFileSync } from 'fs'
import { dirname, resolve } from 'path'
import { fileURLToPath } from 'url'

// This file runs as ESM ("type": "module"), where __dirname is undefined.
const HERE = dirname(fileURLToPath(import.meta.url))
const BASE = process.env.E2E_API_BASE ?? 'http://localhost:28000'
const SEED_FILE = resolve(HERE, '.e2e-seed.json')

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

  // Raise the rate limits for the test run. The AUTH bucket defaults to 10
  // req/min per IP, but the SPA re-authenticates via /api/auth/refresh on every
  // full page load, so a multi-test suite (a login plus a boot refresh per
  // navigation) trips it mid-run and bounces tests to /login — the historic e2e
  // login flakiness. OTHER is the fall-through bucket for everything that is not
  // auth/chat-send/upload and defaults to 300/min per *user*; the suite drives a
  // single seeded user, so it exhausts that around thirty specs in and the next
  // write 429s, failing whichever spec happens to be running.
  // Lifting them here (admin-only, against the test backend) removes both at the
  // source; production keeps the strict defaults. Scope is kept as each bucket
  // defines it — `other` is user-scoped, not IP-scoped. Non-fatal: the auth
  // fixture also backs off on 429, so a failure here only makes the suite
  // slower, not red.
  const RATE_LIMITS = [
    { key: 'auth', scope: 'ip', maxCount: 1000 },
    { key: 'auth-recovery', scope: 'ip', maxCount: 1000 },
    { key: 'other', scope: 'user', maxCount: 100_000 },
  ]
  for (const limit of RATE_LIMITS) {
    const r = await api.patch(`/api/admin/rate-limits/${limit.key}`, {
      headers: adminAuth,
      data: { max_count: limit.maxCount, window_sec: 60, scope: limit.scope },
    })
    if (!r.ok()) {
      console.warn(`[e2e-seed] could not raise "${limit.key}" rate limit (status ${r.status()})`)
    }
  }

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

      // Create an org-owned project. The route is POST /api/projects with the
      // owner descriptor in the body (NOT /api/orgs/{id}/projects).
      const projResp = await api.post('/api/projects', {
        headers: userAuth,
        data: { name: `e2e-proj-${Date.now()}`, owner_type: 'org', owner_id: org.id },
      })
      if (projResp.ok()) {
        const proj = await projResp.json()
        seed.E2E_PROJECT_ID = proj.id

        // A key group is required to create an agent — make it first.
        const kgResp = await api.post(`/api/projects/${proj.id}/key-groups`, {
          headers: userAuth,
          data: { name: `e2e-keygroup-${Date.now()}` },
        })
        let keyGroupId: string | undefined
        if (kgResp.ok()) {
          const kg = await kgResp.json()
          keyGroupId = kg.id
          seed.E2E_KEY_GROUP_URL = `projects/${proj.id}/key-groups/${kg.id}`
          // Exposed separately from the URL because specs that create an agent
          // must select THIS group by id: the picker defaults to the first
          // group, and the list is ordered created_at DESC, so any group made
          // later by another spec would otherwise be chosen — and it would not
          // carry the OpenAI key the agent's model_hint requires.
          seed.E2E_KEY_GROUP_ID = kg.id
        } else {
          console.warn(
            `[e2e-seed] KEY GROUP creation failed (status ${kgResp.status()}): ${await kgResp.text()}`,
          )
        }

        // An agent's key group must hold a carried key matching the agent's
        // model_hint (agent_service._assert_key_group_has_provider), so the
        // key has to exist, be carried into the project, and be a group member
        // before the agent POST below can succeed. The backend live-probes the
        // secret on upload; the test stack answers those probes from the
        // fake-provider service (see deploy/compose/compose.test.yml).
        let keyId: string | undefined
        if (keyGroupId) {
          const keyResp = await api.post('/api/keys', {
            headers: userAuth,
            data: {
              provider: 'openai',
              name: `e2e-key-${Date.now()}`,
              secret: `sk-e2e-fake-${Date.now()}`,
            },
          })
          if (keyResp.ok()) {
            const key = await keyResp.json()
            keyId = key.id
            seed.E2E_KEY_ID = key.id
            // Upload succeeds even when the probe fails (the row is kept so the
            // user can retest), so status is checked separately — a `failed`
            // key here means the fake provider was unreachable.
            if (key.test_status !== 'ok') {
              console.warn(
                `[e2e-seed] key probe returned test_status=${key.test_status}` +
                  ` (${key.test_error ?? 'no error'}) — is the fake-provider service up` +
                  ' and are SMAP_PROBE_*_BASE_URL set on backend-web?',
              )
            }
          } else {
            console.warn(
              `[e2e-seed] KEY upload failed (status ${keyResp.status()}): ${await keyResp.text()}`,
            )
          }
        }

        if (keyId) {
          const carryResp = await api.post(`/api/projects/${proj.id}/keys`, {
            headers: userAuth,
            data: { key_id: keyId },
          })
          if (!carryResp.ok()) {
            console.warn(
              `[e2e-seed] KEY carry into project failed (status ${carryResp.status()}):` +
                ` ${await carryResp.text()}`,
            )
            keyId = undefined
          }
        }

        if (keyId && keyGroupId) {
          const memberResp = await api.post(`/api/key-groups/${keyGroupId}/keys`, {
            headers: userAuth,
            data: { key_id: keyId },
          })
          if (!memberResp.ok()) {
            console.warn(
              `[e2e-seed] KEY group membership failed (status ${memberResp.status()}):` +
                ` ${await memberResp.text()}`,
            )
            keyId = undefined
          }
        }

        // Create agent in project. Required: model_hint (claude|openai|gemini)
        // and key_group_id — there is no 'provider' field. model_hint must
        // match the provider of the key carried into the group above.
        if (keyGroupId) {
          const agentResp = await api.post(`/api/projects/${proj.id}/agents`, {
            headers: userAuth,
            data: {
              name: `e2e-agent-${Date.now()}`,
              model_hint: 'openai',
              model_id: 'gpt-4o-mini',
              key_group_id: keyGroupId,
              system_prompt: 'You are a test agent.',
            },
          })
          if (agentResp.ok()) {
            const agent = await agentResp.json()
            seed.E2E_AGENT_ID = agent.id
          } else {
            // Loud on purpose: a missing E2E_AGENT_ID silently skips every
            // agent-dependent spec, so CI stays green while losing coverage.
            console.warn(
              `[e2e-seed] AGENT creation FAILED (status ${agentResp.status()}):` +
                ` ${await agentResp.text()}\n` +
                '[e2e-seed] E2E_AGENT_ID will be absent — all agent-dependent specs' +
                ' will SKIP. Check the key upload/carry/membership warnings above.',
            )
          }
        }
      }
    }

    // Get or create workspace (project-scoped)
    const projectId = seed.E2E_PROJECT_ID
    const wsListResp = projectId
      ? await api.get(`/api/projects/${projectId}/workspaces`, { headers: userAuth })
      : null
    if (wsListResp?.ok()) {
      const workspaces = await wsListResp.json()
      let ws = workspaces[0]
      if (!ws) {
        const wsResp = await api.post(`/api/projects/${projectId}/workspaces`, {
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
              schema_version: '1.0',
              name: 'E2E Test Workflow',
              variables: {},
              timeouts: { run_max_seconds: 60, idle_max_seconds: 30 },
              entry_node_id: 'trigger_1',
              nodes: [
                { id: 'trigger_1', type: 'trigger', config: { trigger_type: 'manual', allowed_roles: ['ProjectOwner'] }, label: 'Start', position: { x: 0, y: 0 } },
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
  } catch (err) {
    console.warn('[e2e-seed] Seeding error (tests with env gates will skip):', err)
  }

  const count = Object.keys(seed).length
  console.log(`[e2e-seed] Seeded ${count} entities:`, Object.keys(seed).join(', '))

  // Every gate below silently skips specs when its ID is missing. Name them
  // explicitly so a regression shows up as a visible CI warning rather than a
  // green run with quietly reduced coverage.
  const REQUIRED = [
    'E2E_ORG_ID',
    'E2E_PROJECT_ID',
    'E2E_AGENT_ID',
    'E2E_WORKSPACE_ID',
    'E2E_CHATROOM_ID',
    'E2E_WORKFLOW_ID',
  ]
  const missing = REQUIRED.filter((k) => !seed[k])
  if (missing.length > 0) {
    console.warn(
      `[e2e-seed] MISSING seed IDs: ${missing.join(', ')} — specs gated on these will SKIP.`,
    )
  }

  writeFileSync(SEED_FILE, JSON.stringify(seed, null, 2), 'utf-8')
  await api.dispose()
}

export default globalSetup
