import { test, expect } from './fixtures/auth'

test.describe('Two-browser chatroom live; edit window; moderator edit', () => {
  test('send and receive a message', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_CHATROOM_ID, 'needs seeded chatroom')
    const chatroomId = process.env.E2E_CHATROOM_ID!
    await page.goto(`/chatrooms/${chatroomId}`)
    const composer = page.getByRole('textbox', { name: /type a message/i })
    await composer.fill('Hello E2E')
    await page.getByRole('button', { name: /send/i }).click()
    await expect(page.getByText('Hello E2E')).toBeVisible({ timeout: 10_000 })
  })

  test('two-browser live sync', async ({ authedPage, adminPage }) => {
    test.skip(!process.env.E2E_CHATROOM_ID, 'needs seeded chatroom')
    const chatroomId = process.env.E2E_CHATROOM_ID!
    await authedPage.goto(`/chatrooms/${chatroomId}`)
    await adminPage.goto(`/chatrooms/${chatroomId}`)
    const msg = `sync-${Date.now()}`
    await authedPage.getByRole('textbox', { name: /type a message/i }).fill(msg)
    await authedPage.getByRole('button', { name: /send/i }).click()
    await expect(adminPage.getByText(msg)).toBeVisible({ timeout: 10_000 })
  })
})
