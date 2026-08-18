import type { Page } from '@playwright/test'

import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

/**
 * Delegating activity start/end authority to a bound agent ([R30.37], AC-19).
 *
 * WHAT THIS COVERS, AND WHAT IT DELIBERATELY DOES NOT
 * ---------------------------------------------------
 * The grant lifecycle end to end through the real stack: the room creator grants
 * it in the settings UI, it round-trips through the API and the database, an
 * empty allowlist is refused, and a revoke sticks. That is the first and last
 * step of AC-19's manual script, and the two that a browser can settle.
 *
 * It does **not** drive an agent actually calling `start_activity`. That needs a
 * live provider key and a model that decides, on its own judgement, to call the
 * tool — which is the very thing §10 R-2 records as untestable ("a test cannot
 * establish that an agent obeys its prompt"). Asserting on it would mean stubbing
 * the model, at which point the test proves the stub called the tool and nothing
 * about the feature. The delegated call path itself is covered deterministically
 * by `backend/tests/unit/test_activity_control_tools.py`, including that the
 * broadcast only leaves after the turn commits.
 *
 * Copy resolved from src/slices/conversation/locales/en.json:
 *   activityControl.label     -> "May start and end activities"
 *   activityControl.allowlist -> "Activities this agent may run"
 *   activityControl.apply     -> "Apply"
 */
const GRANT_LABEL = /May start and end activities/
const APPLY = /^Apply$/

const CHATROOM_ID = env('E2E_CHATROOM_ID')
const PROJECT_ID = env('E2E_PROJECT_ID')
const AGENT_ID = env('E2E_AGENT_ID')

/** The grant control for the seeded agent, once the settings page has loaded. */
async function openSettings(page: Page, chatroomId: string) {
  await page.goto(`/chatrooms/${chatroomId}/settings`)
  const toggle = page.getByRole('switch', { name: GRANT_LABEL })
  await expect(toggle).toBeVisible({ timeout: 20_000 })
  return toggle
}

/**
 * Ensure the room binds the seeded agent and the project has one activity type.
 *
 * Through `page.request`, which carries the page's own session cookies — so these
 * calls are made as the same room creator the UI is driven as, and hit the same
 * authorization the UI would. Both are idempotent enough to re-run: a repeat bind
 * is `ON CONFLICT DO NOTHING`, and a repeat type registration is refused on the
 * key, which is fine because the listing below is what the test actually needs.
 */
async function ensureFixtures(page: Page, chatroomId: string, projectId: string, agentId: string) {
  await page.request.post(`/api/chatrooms/${chatroomId}/agents`, {
    data: { agent_id: agentId },
  })
  const listed = await page.request.get(`/api/projects/${projectId}/activity-types`)
  const existing: Array<{ id: string; name: string }> = listed.ok() ? await listed.json() : []
  if (existing.length > 0) return existing[0]!

  const created = await page.request.post(`/api/projects/${projectId}/activity-types`, {
    data: {
      key: 'e2e-delegated-control',
      name: 'E2E delegated control worksheet',
      payload_schema: { type: 'object', properties: { answer: { type: 'string' } } },
      validator_kind: 'in_process',
      validator_config: { validator_id: 'filled_count', min_filled: 1 },
      retention_days: null,
    },
  })
  expect(created.ok(), `activity type creation failed: ${await created.text()}`).toBeTruthy()
  return (await created.json()) as { id: string; name: string }
}

test.describe('Delegated activity control', () => {
  test.skip(
    !CHATROOM_ID || !PROJECT_ID || !AGENT_ID,
    'needs a seeded chatroom, project and agent',
  )

  test('a room creator grants, the grant survives a reload, and a revoke sticks', async ({
    authedPage: page,
  }) => {
    const activityType = await ensureFixtures(page, CHATROOM_ID!, PROJECT_ID!, AGENT_ID!)

    // --- grant -----------------------------------------------------------
    const toggle = await openSettings(page, CHATROOM_ID!)
    await expect(toggle).toHaveAttribute('aria-checked', 'false')
    await toggle.click()

    const allowlist = page.getByRole('group', { name: /Activities this agent may run/ })
    await expect(allowlist).toBeVisible()
    await allowlist.getByRole('checkbox', { name: activityType.name }).check()

    const grantWrite = page.waitForResponse(
      (r) => r.url().includes('/activity-control') && r.request().method() === 'PATCH',
    )
    await page.getByRole('button', { name: APPLY }).click()
    expect((await grantWrite).status()).toBe(204)

    // --- it round-trips ---------------------------------------------------
    // A reload, not just the in-page refetch: what has to hold is that the row
    // was written, not that the component kept its own draft.
    const reloaded = await openSettings(page, CHATROOM_ID!)
    await expect(reloaded).toHaveAttribute('aria-checked', 'true')
    await expect(
      page.getByRole('group', { name: /Activities this agent may run/ })
        .getByRole('checkbox', { name: activityType.name }),
    ).toBeChecked()

    // --- revoke -----------------------------------------------------------
    await reloaded.click()
    const revokeWrite = page.waitForResponse(
      (r) => r.url().includes('/activity-control') && r.request().method() === 'PATCH',
    )
    await page.getByRole('button', { name: APPLY }).click()
    expect((await revokeWrite).status()).toBe(204)

    const afterRevoke = await openSettings(page, CHATROOM_ID!)
    await expect(afterRevoke).toHaveAttribute('aria-checked', 'false')
  })

  test('granting with nothing selected is refused before it reaches the server', async ({
    authedPage: page,
  }) => {
    // The server answers 422 and the DB CHECK refuses the same state; the client
    // refusing first is what turns an opaque error into a usable one.
    await ensureFixtures(page, CHATROOM_ID!, PROJECT_ID!, AGENT_ID!)
    const toggle = await openSettings(page, CHATROOM_ID!)
    if ((await toggle.getAttribute('aria-checked')) === 'true') {
      // Leave the room in the ungranted state this case needs, whatever the
      // previous test left behind — specs share one seeded room.
      await toggle.click()
      await page.getByRole('button', { name: APPLY }).click()
      await page.waitForResponse((r) => r.url().includes('/activity-control'))
      await openSettings(page, CHATROOM_ID!)
    }

    let wrote = false
    page.on('request', (r) => {
      if (r.url().includes('/activity-control')) wrote = true
    })

    await page.getByRole('switch', { name: GRANT_LABEL }).click()
    // Nothing ticked, so the draft is granted-with-an-empty-allowlist.
    await page.getByRole('button', { name: APPLY }).click()
    await page.waitForTimeout(500)

    expect(wrote).toBe(false)
    await expect(page.getByRole('alert')).toContainText(/at least one activity/i)
  })
})
