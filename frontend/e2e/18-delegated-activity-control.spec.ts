import type { APIRequestContext, Page } from '@playwright/test'

import { test, expect, bearerFor } from './fixtures/auth'
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
 * Made as the seeded user — the same room creator the UI is driven as, so these
 * hit the same authorization the UI would — but through Playwright's own
 * `request` context and a bearer token, not `page.request`: the page's cookie
 * jar holds only the refresh token, so a call made on it is anonymous (see
 * `bearerFor`). Both calls are idempotent enough to re-run: a repeat bind is
 * `ON CONFLICT DO NOTHING`, and a repeat type registration is refused on the
 * key, which is fine because the listing below is what the test actually needs.
 */
async function ensureFixtures(
  api: APIRequestContext,
  chatroomId: string,
  projectId: string,
  agentId: string,
) {
  const headers = await bearerFor(api)
  const bound = await api.post(`/api/chatrooms/${chatroomId}/agents`, {
    headers,
    data: { agent_id: agentId },
  })
  expect(bound.ok(), `agent bind failed: ${await bound.text()}`).toBeTruthy()

  const listed = await api.get(`/api/projects/${projectId}/activity-types`, { headers })
  // Asserted, not defaulted to `[]`: a failure here used to look like "no types
  // yet" and surfaced only as a confusing failure on the creation below.
  expect(listed.ok(), `activity type listing failed: ${await listed.text()}`).toBeTruthy()
  const existing = (await listed.json()) as Array<{ id: string; name: string }>
  if (existing.length > 0) return existing[0]!

  const created = await api.post(`/api/projects/${projectId}/activity-types`, {
    headers,
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
    request,
  }) => {
    const activityType = await ensureFixtures(request, CHATROOM_ID!, PROJECT_ID!, AGENT_ID!)

    // --- grant -----------------------------------------------------------
    const toggle = await openSettings(page, CHATROOM_ID!)
    await expect(toggle).toHaveAttribute('aria-checked', 'false')
    await toggle.click()

    const allowlist = page.getByRole('group', { name: /Activities this agent may run/ })
    await expect(allowlist).toBeVisible()
    // Ticked through the label, which is what a user clicks: `SCheckbox`'s native
    // input is screen-reader-only (absolutely positioned, 1px, clipped) and sits
    // under the styled box, so `.check()` on it never gets past Playwright's
    // hit-target test — the box on top is a sibling, not its descendant.
    const worksheet = allowlist.getByRole('checkbox', { name: activityType.name })
    await allowlist.getByText(activityType.name, { exact: true }).click()
    await expect(worksheet).toBeChecked()

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
    request,
  }) => {
    // The server answers 422 and the DB CHECK refuses the same state; the client
    // refusing first is what turns an opaque error into a usable one.
    await ensureFixtures(request, CHATROOM_ID!, PROJECT_ID!, AGENT_ID!)
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

    // Flipping the switch on is not enough to reach the state under test: a
    // revoke leaves the stored allowlist in place server-side (so a re-grant
    // keeps the teacher's selection), and the draft is seeded from it — so the
    // boxes come back already ticked from whatever was granted before. Clear
    // them, through the label for the reason the first test gives.
    const allowlist = page.getByRole('group', { name: /Activities this agent may run/ })
    await expect(allowlist).toBeVisible()
    const rows = allowlist.locator('label')
    for (let i = 0; i < (await rows.count()); i++) {
      const row = rows.nth(i)
      if (await row.getByRole('checkbox').isChecked()) await row.click()
      await expect(row.getByRole('checkbox')).not.toBeChecked()
    }

    await page.getByRole('button', { name: APPLY }).click()
    await page.waitForTimeout(500)

    expect(wrote).toBe(false)
    await expect(page.getByRole('alert')).toContainText(/at least one activity/i)
  })
})
