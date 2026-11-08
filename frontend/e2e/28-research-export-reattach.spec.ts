import type { APIRequestContext } from '@playwright/test'
import { bearerFor, test, expect } from './fixtures/auth'
import { env } from './fixtures/seed'

// A finished research export stays reachable after the dialog that started it
// is gone: from the notification's link and by reopening the dialog
// (docs/tasks/2026-11-08-research-data-integrity AC-2).

async function readyExport(api: APIRequestContext, workspaceId: string): Promise<string> {
  const auth = await bearerFor(api)
  const created = await api.post(`/api/workspaces/${workspaceId}/export/research-data`, {
    headers: auth,
    data: {},
  })
  // A 409 names the export already running for this owner, which serves as well.
  expect(created.ok() || created.status() === 409, `create -> ${created.status()}`).toBe(true)
  const jobId = ((await created.json()) as { job_id: string }).job_id
  await expect
    .poll(
      async () => {
        const res = await api.get(`/api/exports/research/${jobId}`, { headers: auth })
        return res.ok() ? ((await res.json()) as { status: string }).status : `http ${res.status()}`
      },
      { timeout: 100_000, intervals: [2000] },
    )
    .toBe('ready')
  return jobId
}

test.describe('Research export: re-attach to a finished job', () => {
  test('the notification link and a reopened dialog both offer Download', async ({
    authedPage: page,
    request: api,
  }) => {
    test.setTimeout(150_000)
    test.skip(!env('E2E_WORKSPACE_ID'), 'needs seeded workspace')
    const workspaceId = env('E2E_WORKSPACE_ID')!
    const jobId = await readyExport(api, workspaceId)

    await page.goto(`/workspaces/${workspaceId}/dashboard?researchExport=${jobId}`)
    await expect(page.getByTestId('research-export-download')).toBeVisible({ timeout: 20_000 })

    await page.goto(`/workspaces/${workspaceId}/dashboard`)
    await page.getByTestId('research-export-open').click()
    const download = page.getByTestId('research-export-download')
    await expect(download).toBeVisible({ timeout: 20_000 })

    const [file] = await Promise.all([page.waitForEvent('download'), download.click()])
    expect(file.suggestedFilename()).toMatch(/\.zip$/)
  })
})
