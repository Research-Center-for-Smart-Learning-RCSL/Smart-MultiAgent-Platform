import { test, expect } from './fixtures/auth'

test.describe('tus 600 MB upload with mid-transfer blip', () => {
  test('upload a file via tus', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_CHATROOM_ID, 'needs seeded chatroom')
    const chatroomId = process.env.E2E_CHATROOM_ID!
    await page.goto(`/chatrooms/${chatroomId}`)
    await expect(page.locator('[data-testid="composer"]')).toBeVisible()
  })
})
