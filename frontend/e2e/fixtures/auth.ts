import { test as base, type Page } from '@playwright/test'

export interface TestUser {
  email: string
  password: string
}

export const seedUser: TestUser = {
  email: 'e2e-user@smap.test',
  password: 'E2eP@ssw0rd!Str0ng',
}

export const seedAdmin: TestUser = {
  email: 'e2e-admin@smap.test',
  password: 'E2eAdm1n!Str0ng',
}

async function login(page: Page, user: TestUser): Promise<void> {
  await page.goto('/login')
  await page.getByLabel(/email/i).fill(user.email)
  await page.getByLabel(/password/i).fill(user.password)
  await page.getByRole('button', { name: /log\s*in|sign\s*in|submit/i }).click()
  await page.waitForURL(/(?!.*login).*/)
}

export const test = base.extend<{ authedPage: Page; adminPage: Page }>({
  authedPage: async ({ page }, use) => {
    await login(page, seedUser)
    await use(page)
  },
  adminPage: async ({ browser }, use) => {
    const ctx = await browser.newContext()
    const page = await ctx.newPage()
    await login(page, seedAdmin)
    await use(page)
    await ctx.close()
  },
})

export { expect } from '@playwright/test'
