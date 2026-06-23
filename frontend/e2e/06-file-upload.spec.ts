import { test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

test.describe('tus file upload with progress tracking', () => {
  test('upload a file via drag-and-drop', async ({ authedPage: page }) => {
    test.skip(!env('E2E_CHATROOM_ID'), 'needs seeded chatroom')
    const chatroomId = env('E2E_CHATROOM_ID')!
    await page.goto(`/chatrooms/${chatroomId}`)

    const composer = page.locator('form.composer')
    await expect(composer).toBeVisible({ timeout: 10_000 })

    // Create a synthetic file and drop it onto the composer textarea.
    const textarea = composer.locator('textarea')
    const dataTransfer = await page.evaluateHandle(() => {
      const dt = new DataTransfer()
      const content = 'E2E test file content — ' + Date.now()
      const file = new File([content], 'e2e-upload.txt', { type: 'text/plain' })
      dt.items.add(file)
      return dt
    })

    await textarea.dispatchEvent('drop', { dataTransfer })
    await dataTransfer.dispose()

    // Progress indicator should appear in the attachments list.
    const attachments = composer.locator('ul.attachments li')
    await expect(attachments.first()).toBeVisible({ timeout: 10_000 })
    await expect(attachments.first()).toContainText('e2e-upload.txt')
  })

  test('send a text message', async ({ authedPage: page }) => {
    test.skip(!env('E2E_CHATROOM_ID'), 'needs seeded chatroom')
    const chatroomId = env('E2E_CHATROOM_ID')!
    await page.goto(`/chatrooms/${chatroomId}`)

    const composer = page.locator('form.composer')
    await expect(composer).toBeVisible({ timeout: 10_000 })

    await composer.locator('textarea').fill('E2E test message')
    await composer.locator('button[type="submit"]').click()

    // Message should appear in the list.
    await expect(page.locator('.messages li').last()).toContainText('E2E test message', {
      timeout: 5000,
    })
  })
})
