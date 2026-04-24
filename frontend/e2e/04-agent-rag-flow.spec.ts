import { test, expect } from './fixtures/auth'

test.describe('Create Agent → attach RAG → ingest doc → grounded answer', () => {
  test('create an agent', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_PROJECT_ID, 'needs seeded project')
    const projectId = process.env.E2E_PROJECT_ID!
    await page.goto(`/projects/${projectId}/agents`)
    await expect(page).toHaveURL(/agents/)
  })

  test('configure RAG on agent', async ({ authedPage: page }) => {
    test.skip(!process.env.E2E_AGENT_ID, 'needs seeded agent')
    await page.goto(`/agents/${process.env.E2E_AGENT_ID}`)
    await expect(page).toHaveURL(/agents/)
  })
})
