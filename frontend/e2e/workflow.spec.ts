import { execFileSync } from 'node:child_process'
import { mkdirSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import AxeBuilder from '@axe-core/playwright'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import type { Workflow } from '../src/api/client'

const token = process.env.WORKFLOW_TOKEN
const project = process.env.DEMO_COMPOSE_PROJECT
const backend = `http://127.0.0.1:${process.env.WORKFLOW_API_PORT ?? '8000'}`
const expected = JSON.parse(readFileSync('../fixtures/demo/expected.json', 'utf8'))

async function connect(page: Page, value = token) {
  if (!value) throw new Error('Set the local demo token')
  await page.getByLabel('Local API token').fill(value)
  await page.getByRole('button', { name: 'Connect' }).click()
}

async function scan(page: Page) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa']).analyze()
  expect(results.violations.map(({ id, nodes }) => ({ id, targets: nodes.map(node => node.target) }))).toEqual([])
}

async function capture(page: Page, name: string) {
  const dir = process.env.WORKFLOW_SCREENSHOTS
  if (!dir) return
  mkdirSync(dir, { recursive: true })
  await page.screenshot({ path: resolve(dir, name + '.png'), fullPage: true, animations: 'disabled' })
}

test('runs the five real stages, reconciles a lost response, and reopens after restart', async ({ page, browser, request }) => {
  if (!token || !project) throw new Error('Set WORKFLOW_TOKEN and DEMO_COMPOSE_PROJECT for a fresh demo stack')
  const seenUrls: string[] = []
  page.on('request', req => seenUrls.push(req.url()))
  await page.goto('/workflows')
  await scan(page)
  await connect(page, 'invalid-demo-token')
  await expect(page.getByRole('alert')).toContainText('local API token')
  await connect(page)
  await expect(page.getByRole('heading', { name: 'Workflow history' })).toBeVisible()
  await scan(page)
  await page.getByRole('link', { name: 'New workflow' }).first().click()
  await expect(page.locator('#main')).toBeFocused()
  await page.getByLabel('Left source').selectOption('demo-source-a')
  await page.getByLabel('Right source').selectOption('demo-source-a')
  await expect(page.getByRole('button', { name: 'Register workflow' })).toBeDisabled()
  await page.getByLabel('Right source').selectOption('demo-source-b')
  await scan(page)
  await page.getByRole('button', { name: 'Register workflow' }).click()
  await expect(page.getByRole('heading', { name: 'Execution stages' })).toBeVisible()
  const path = new URL(page.url()).pathname
  const id = Number(path.split('/').at(-1))
  const apiUrl = `/v1/workflows/${id}`
  const fetchWorkflow = async () => (await request.get(apiUrl, {
    headers: { Authorization: `Bearer ${token}` },
  })).json() as Promise<Workflow>
  expect((await fetchWorkflow()).stages.map(stage => stage.attempt_count)).toEqual([0, 0, 0, 0, 0])

  const second = await browser.newPage({ baseURL: new URL(page.url()).origin })
  try {
    // Capture the real initial snapshot to keep this tab stale while the first advances.
    const initial = await fetchWorkflow()
    await second.route(`**${apiUrl}`, route => route.fulfill({ json: initial }))
    await second.goto(path)
    await connect(second)
    await expect(second.getByRole('button', { name: 'Advance one stage' })).toBeEnabled()
    await page.route(`**${apiUrl}/advance`, async route => {
      const committed = await route.fetch()
      expect(committed.ok()).toBe(true)
      await route.abort('failed')
    }, { times: 1 })
    await page.getByRole('button', { name: 'Advance one stage' }).click()
    await expect(page.getByText('1 of 5 stages completed')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Advance one stage' })).toBeEnabled()
    const stale = second.waitForResponse(response => response.url().endsWith('/advance'))
    await second.getByRole('button', { name: 'Advance one stage' }).click()
    expect((await (await stale).json()).error.code).toBe('STAGE_STATE_CHANGED')
    expect((await fetchWorkflow()).stages.map(stage => stage.attempt_count)).toEqual([1, 0, 0, 0, 0])
  } finally { await second.close() }

  for (let n = 2; n <= 5; n++) {
    await page.getByRole('button', { name: 'Advance one stage' }).click()
    await expect(page.getByText(`${n} of 5 stages completed`)).toBeVisible()
  }
  await scan(page)
  await capture(page, 'phase5-execution')
  const before = await fetchWorkflow()
  expect(before.stages.map(stage => stage.attempt_count)).toEqual([1, 1, 1, 1, 1])
  expect(before.inputs.map(input => input.manifest)).toEqual(Object.values(expected.manifests))

  await page.getByRole('link', { name: 'Quality findings' }).click()
  await expect(page.getByRole('heading', { name: 'No project quality findings for this source' })).toBeVisible()
  await page.getByRole('link', { name: 'Right source' }).click()
  await expect(page.getByRole('article')).toHaveCount(expected.findings.right)
  await page.getByText('Finding evidence', { exact: true }).click()
  const evidence = page.getByRole('link', { name: /entry/ })
  const target = new URL((await evidence.getAttribute('href'))!, page.url())
  expect(target.searchParams.get('side')).toBe('right')
  expect(target.searchParams.get('batch')).toBe(String(before.stages[1].batch_id))
  await scan(page)
  await evidence.click()
  await expect(page.getByRole('heading', { name: 'Linked source evidence' })).toBeVisible()
  await expect(page.locator('.linked-file')).toContainText(target.searchParams.get('sha')!)
  await expect(page.locator('.linked-file')).toContainText(`entry ${target.searchParams.get('entry')}`)
  await scan(page)

  await page.getByRole('link', { name: 'Matching' }).click()
  await expect(page.getByRole('heading', { name: 'Candidate pair' })).toHaveCount(expected.candidates)
  await expect(page.getByText('Suggested match · review required', { exact: true }))
    .toHaveCount(expected.exact_suggestions + expected.statistical_suggestions)
  await expect(page.getByText('Right without a candidate').locator('..')).toContainText(String(expected.no_candidate_right))
  await expect(page.getByText('Left without a candidate').locator('..')).toContainText(String(expected.no_candidate_left))
  const pairRight = new URL((await page.getByRole('link', { name: /^Right ·/ }).getAttribute('href'))!, page.url())
  expect(pairRight.searchParams.get('batch')).toBe(String(before.stages[1].batch_id))
  await page.getByText('Comparison evidence', { exact: true }).click()
  await scan(page)
  await capture(page, 'phase5-matching')

  await page.setViewportSize({ width: 375, height: 812 })
  await page.getByRole('link', { name: 'Quality findings' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.locator('#main')).toBeFocused()
  await page.getByRole('link', { name: 'Right source' }).focus()
  await page.keyboard.press('Enter')
  await expect(page.getByRole('article')).toHaveCount(1)
  await page.getByText('Finding evidence', { exact: true }).focus()
  await page.keyboard.press('Enter')
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(375)
  await scan(page)
  await capture(page, 'phase5-quality-mobile')

  const env = { ...process.env }
  execFileSync('docker', ['compose', '-p', project, '--profile', 'workflow', 'restart', 'api'], {
    cwd: '..', env, stdio: 'ignore',
  })
  await expect.poll(async () => {
    try { return (await request.get(backend + '/health/ready')).ok() } catch { return false }
  }, { timeout: 15_000 }).toBe(true)
  await page.reload()
  await expect(page.getByLabel('Local API token')).toBeVisible()
  await connect(page)
  await expect(page.getByRole('heading', { name: 'Data quality findings' })).toBeVisible()
  expect((await fetchWorkflow()).stages).toEqual(before.stages)
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length, document.cookie])).toEqual([0, 0, ''])
  expect(seenUrls.some(url => url.includes(token))).toBe(false)
  await page.getByRole('button', { name: 'Disconnect' }).click()
  await expect(page.getByLabel('Local API token')).toBeVisible()
})

test('proxy rejects unauthorized and cross-origin requests', async ({ request }) => {
  const unauthorized = await request.get('/v1/catalog')
  expect(unauthorized.status()).toBe(401)
  expect(unauthorized.headers()['cache-control']).toBe('no-store')
  expect((await request.get('/v1/catalog', { headers: { Origin: 'http://127.0.0.1:9999' } })).status()).toBe(403)
  expect((await request.get('/v1/catalog', { headers: { Host: 'example.invalid' } })).status()).toBe(403)
  expect((await request.get('/v1/arbitrary')).status()).toBe(404)
})
