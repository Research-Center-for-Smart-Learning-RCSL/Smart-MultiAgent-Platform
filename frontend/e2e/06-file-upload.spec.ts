import { test, expect } from './fixtures/auth'

test.describe('tus file upload with progress tracking', () => {
  test('upload a file via drag-and-drop', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_CHATROOM_ID, 'needs seeded chatroom')
    const chatroomId = process.env.E2E_CHATROOM_ID!
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

    // Progress indicator should appear in the attachments list.
    const attachments = composer.locator('ul.attachments li')
    await expect(attachments.first()).toBeVisible({ timeout: 10_000 })
    await expect(attachments.first()).toContainText('e2e-upload.txt')
  })

  test('send message with attachment', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_CHATROOM_ID, 'needs seeded chatroom')
    const chatroomId = process.env.E2E_CHATROOM_ID!
    await page.goto(`/chatrooms/${chatroomId}`)

    const composer = page.locator('form.composer')
    await expect(composer).toBeVisible({ timeout: 10_000 })

    // Type a message.
    const textarea = composer.locator('textarea')
    await textarea.fill('E2E attachment test message')

    // Submit the message.
    await composer.locator('button[type="submit"]').click()

    // Message should appear in the list.
    await expect(page.locator('.messages li').last()).toContainText('E2E attachment test message', {
      timeout: 5000,
    })
  })

  test('composer is functional', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_CHATROOM_ID, 'needs seeded chatroom')
    const chatroomId = process.env.E2E_CHATROOM_ID!
    await page.goto(`/chatrooms/${chatroomId}`)

    // Verify composer elements are present and interactive.
    const composer = page.locator('form.composer')
    await expect(composer).toBeVisible({ timeout: 10_000 })
    await expect(composer.locator('textarea')).toBeEnabled()
    await expect(composer.locator('button[type="submit"]')).toBeEnabled()
  })
})
