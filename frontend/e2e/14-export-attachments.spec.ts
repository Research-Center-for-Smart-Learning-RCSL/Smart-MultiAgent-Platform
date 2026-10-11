import { statSync } from 'node:fs'
import type { Page } from '@playwright/test'
import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

// The header's export control (data-testid="open-export", aria-label "Export")
// only opens ChatroomExportModal — the job is created by the modal's own
// "Export" footer button (data-testid="submit-export"). Both carry the same
// accessible name, so drive them by test id.
async function startExport(page: Page) {
  await page.getByTestId('open-export').click()
  const modal = page.getByRole('dialog')
  await expect(modal).toBeVisible()
  await modal.getByTestId('submit-export').click()
  return modal
}

test.describe('Export + attachments: download + expired state (M.3/M.5)', () => {
  test('trigger an export and see status', async ({ authedPage: page }) => {
    test.skip(!env('E2E_CHATROOM_ID'), 'needs seeded chatroom')
    const chatroomId = env('E2E_CHATROOM_ID')!
    await page.goto(`/chatrooms/${chatroomId}`)

    const modal = await startExport(page)

    // Export status replaces the form once a job exists (queued/running → ready
    // or failed).
    await expect(modal.getByTestId('export-status')).toBeVisible({ timeout: 10_000 })
  })

  test('a ready export downloads a non-empty file without leaving the room', async ({ authedPage: page }) => {
    test.setTimeout(120_000)
    test.skip(!env('E2E_CHATROOM_ID'), 'needs seeded chatroom')
    const chatroomId = env('E2E_CHATROOM_ID')!
    await page.goto(`/chatrooms/${chatroomId}`)

    const modal = await startExport(page)

    // Only the ready state counts: accepting the failure state here is what
    // let an unreachable download link ship green
    // (docs/audits/2026-10-11-chat-and-research-export F-1, FU-5).
    const download = modal.getByTestId('export-download')
    await expect(download).toBeVisible({ timeout: 100_000 })

    const [file] = await Promise.all([page.waitForEvent('download'), download.click()])
    expect(file.suggestedFilename()).toMatch(/\.md$/)
    const path = await file.path()
    expect(path).toBeTruthy()
    expect(statSync(path).size).toBeGreaterThan(0)
    await expect(page).toHaveURL(new RegExp(`/chatrooms/${chatroomId}`))
  })
  test('message attachments render download or expired state', async ({ authedPage: page }) => {
    test.skip(!env('E2E_CHATROOM_ID'), 'needs seeded chatroom with attachments')
    const chatroomId = env('E2E_CHATROOM_ID')!
    await page.goto(`/chatrooms/${chatroomId}`)

    // ChatroomMessageBubble renders the list as .bubble__attachments. Each <li>
    // holds exactly one of: an AttachmentImage (.attachment-image or its
    // .attachment-image__fallback button) for an active image, a
    // .attachment-link button for any other active file, or a .attachment-gone
    // label for a quarantined/expired one.
    // Messages stream in after the chatroom socket/query settles — wait for an
    // attachment item rather than sampling the count at page load.
    const items = page.locator('.messages .bubble__attachments > li')
    let hasAttachments = true
    try {
      await items.first().waitFor({ state: 'visible', timeout: 10_000 })
    } catch {
      hasAttachments = false
    }
    test.skip(!hasAttachments, 'no messages with attachments')
    await expect(
      items
        .first()
        .locator('.attachment-link, .attachment-gone, .attachment-image, .attachment-image__fallback'),
    ).toBeVisible()
  })
})
