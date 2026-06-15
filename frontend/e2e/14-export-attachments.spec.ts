import { test, expect } from './fixtures/auth'

test.describe('Export + attachments: download + expired state (M.3/M.5)', () => {
  test('trigger an export and see status', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_CHATROOM_ID, 'needs seeded chatroom')
    const chatroomId = process.env.E2E_CHATROOM_ID!
    await page.goto(`/chatrooms/${chatroomId}`)

    const exportBtn = page.getByRole('button', { name: /export/i })
    await expect(exportBtn).toBeVisible()
    await exportBtn.click()

    // Export status should appear (pending → ready or failed).
    await expect(page.locator('.export-status')).toBeVisible({ timeout: 10_000 })
  })

  test('export download link appears when ready', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_CHATROOM_ID, 'needs seeded chatroom with completed export')
    const chatroomId = process.env.E2E_CHATROOM_ID!
    await page.goto(`/chatrooms/${chatroomId}`)

    const exportBtn = page.getByRole('button', { name: /export/i })
    await exportBtn.click()

    // Poll until ready; download link is an <a> with download attribute.
    const downloadLink = page.locator('.export-status a[download]')
    await expect(downloadLink).toBeVisible({ timeout: 60_000 })
  })

  test('message attachments render download or expired state', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_CHATROOM_ID, 'needs seeded chatroom with attachments')
    const chatroomId = process.env.E2E_CHATROOM_ID!
    await page.goto(`/chatrooms/${chatroomId}`)

    // At least one attachment element should be visible (download button or gone label).
    const attachments = page.locator('.messages .attachments')
    test.skip((await attachments.count()) === 0, 'no messages with attachments')
    await expect(
      attachments.first().locator('.link-btn, .attachment-gone'),
    ).toBeVisible()
  })
})
