import { expect, test, type Page, type Response } from '@playwright/test'

// End-to-end checks against the real backend and real ingested ECMWF data.

const isTile = (r: Response) => /\/api\/forecast\/ifs\/\d{10}\/tile\/\d+\/\d+\/\d+\.png/.test(r.url())

async function waitForTiles(page: Page, query: RegExp) {
  return page.waitForResponse((r) => isTile(r) && query.test(r.url()) && r.status() === 200, { timeout: 30_000 })
}

test('map loads genuine IFS tiles, summary and metadata', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  const tile = waitForTiles(page, /start_step=0&end_step=24/)
  await page.goto('/')
  const r = await tile
  expect(r.headers()['content-type']).toBe('image/png')

  await expect(page.getByText(/^Run .* UTC/)).toBeVisible()
  await expect(page.getByText('ECMWF IFS forecast · model grid values, not observed rainfall')).toBeVisible()
  await expect(page.getByRole('radio', { name: /AIFS/ })).toBeDisabled()
  await expect(page.getByText('coming soon')).toBeVisible()

  // Regional summary for the default India preset (values come from the API)
  const summary = page.locator('section', { has: page.getByRole('heading', { name: 'Regional summary' }) })
  await expect(summary.getByText('India', { exact: true })).toBeVisible()
  await expect(summary.getByText(/Heaviest grid cell/)).toBeVisible()
  await expect(summary.getByText(/approximate bounding boxes/)).toBeVisible()

  // Legend from /api/colorscale
  await expect(page.getByRole('list', { name: 'Precipitation colour scale' }).getByRole('listitem')).toHaveCount(13)
  await page.screenshot({ path: 'e2e/screenshots/desktop-india.png' })
  expect(errors).toEqual([])
})

test('timeline and window controls change the tiles', async ({ page }) => {
  const first = waitForTiles(page, /start_step=0&end_step=24/)
  await page.goto('/')
  await first
  const next = waitForTiles(page, /start_step=\d+&end_step=27/)
  await page.getByRole('button', { name: 'Next step' }).click()
  await next
  await expect(page.getByText(/T\+3h → T\+27h/)).toBeVisible()

  const native = waitForTiles(page, /start_step=24&end_step=27/)
  await page.getByRole('radio', { name: 'Step' }).click()
  await native
  await expect(page.getByText(/\(3 h accumulation\)/)).toBeVisible()
})

test('preset selection updates the regional summary; dry box shows no max location', async ({ page }) => {
  await page.goto('/')
  const summary = page.locator('section', { has: page.getByRole('heading', { name: 'Regional summary' }) })
  await expect(summary.getByText(/Heaviest grid cell/)).toBeVisible()
  const resp = page.waitForResponse((r) => r.url().includes('region=bengaluru') && r.status() === 200)
  await page.getByRole('button', { name: 'Bengaluru', exact: true }).click()
  const body = await (await resp).json()
  await expect(summary.getByText('Bengaluru', { exact: true })).toBeVisible()
  if (body.max_location === null) {
    await expect(summary.getByText('No precipitation forecast anywhere in this box for the selected window.')).toBeVisible()
    await expect(summary.getByText(/Heaviest grid cell/)).toHaveCount(0)
  } else {
    await expect(summary.getByText(/Heaviest grid cell/)).toBeVisible()
  }
})

test('location search and map click show the point forecast', async ({ page }) => {
  await page.goto('/')
  await page.getByLabel('Search location').fill('Chennai')
  const option = page.getByRole('button', { name: /^Chennai/ }).first()
  await expect(option).toBeVisible({ timeout: 15_000 })
  const tl = page.waitForResponse((r) => r.url().includes('/timeline?') && r.status() === 200)
  await option.click()
  await tl
  await expect(page.getByText(/grid cell 13\.\d+°, 80\.\d+°/)).toBeVisible()
  await expect(page.getByRole('img', { name: /Precipitation per interval/ })).toBeVisible()
  await expect(page.locator('section', { has: page.getByRole('heading', { name: 'Regional summary' }) }).getByText(/Chennai/)).toBeVisible()

  // Clicking the map also selects a point
  const tl2 = page.waitForResponse((r) => r.url().includes('/timeline?') && r.status() === 200)
  await page.getByRole('region', { name: 'Precipitation map' }).click({ position: { x: 300, y: 300 } })
  await tl2
})

test('mobile layout stacks map, controls and panels', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 })
  const tile = waitForTiles(page, /end_step=24/)
  await page.goto('/')
  await tile
  await expect(page.getByRole('region', { name: 'Precipitation map' })).toBeVisible()
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)
  expect(overflow).toBeLessThanOrEqual(0)
  await page.screenshot({ path: 'e2e/screenshots/mobile.png', fullPage: true })
})
