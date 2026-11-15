import { expect, test, type Page, type Response } from '@playwright/test'

// End-to-end checks against the real backend and real ingested ECMWF data.

const isTile = (r: Response) => /\/api\/forecast\/ifs\/\d{10}\/tile\/\d+\/\d+\/\d+\.png/.test(r.url())

const isAifsTile = (r: Response) => /\/api\/forecast\/aifs-single\/\d{10}\/tile\/\d+\/\d+\/\d+\.png/.test(r.url())
const tile2lon =(x: number, z: number) => (x / 2 ** z) * 360 - 180
const tile2lat = (y: number, z: number) => (Math.atan(Math.sinh(Math.PI * (1 - (2 * y) / 2 ** z))) * 180) / Math.PI
const tileZXY = (url: string) => new URL(url).pathname.split('/tile/')[1].replace('.png', '').split('/').map(Number)

async function waitForTiles(page: Page, query: RegExp) {
  return page.waitForResponse((r) => isTile(r) && query.test(r.url()) && r.status() === 200, { timeout: 30_000 })
}

test('map loads genuine IFS tiles, summary and metadata', async ({ page }) => {
  const errors: string[] = []
  const tileResponses: Response[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('response', (r) => { if (isTile(r)) tileResponses.push(r) })
  const tile = waitForTiles(page, /start_step=0&end_step=24/)
  await page.goto('/')
  const r = await tile
  expect(r.headers()['content-type']).toBe('image/png')
  await page.waitForTimeout(2000)
  expect(tileResponses.length).toBeGreaterThan(4)
  for (const t of tileResponses) {
    expect(t.status(), t.url()).toBe(200)
    expect(t.headers()['content-type']).toBe('image/png')
  }

  await expect(page.getByText(/^Run .* UTC/)).toBeVisible()
  await expect(page.getByText('ECMWF IFS forecast · model grid values, not observed rainfall')).toBeVisible()
  await expect(page.getByRole('radio', { name: /IFS HRES/ })).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByRole('radio', { name: /AIFS/ })).toBeEnabled()

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

test('preset selection moves the map and updates the summary; dry box shows no max location', async ({ page }) => {
  await page.goto('/')
  const summary = page.locator('section', { has: page.getByRole('heading', { name: 'Regional summary' }) })
  await expect(summary.getByText(/Heaviest grid cell/)).toBeVisible()
  const resp = page.waitForResponse((r) => r.url().includes('region=bengaluru') && r.status() === 200)
  // Viewport: after selecting the preset, the map must request zoomed-in tiles covering Bengaluru.
  const zoomed = page.waitForResponse((r) => {
    if (!isTile(r) || r.status() !== 200) return false
    const [z, x, y] = tileZXY(r.url())
    return z >= 7 && tile2lon(x, z) <= 77.59 && tile2lon(x + 1, z) > 77.59 && tile2lat(y, z) >= 12.97 && tile2lat(y + 1, z) < 12.97
  }, { timeout: 30_000 })
  await page.getByRole('button', { name: 'Bengaluru', exact: true }).click()
  const body = await (await resp).json()
  await zoomed
  await expect(summary.getByText('Bengaluru', { exact: true })).toBeVisible()
  // Displayed statistics are exactly the API's values
  await expect(summary.getByText('Mean (area-wtd)').locator('..')).toContainText(body.stats.mean_mm.toFixed(2))
  await expect(summary.getByText('Max cell').locator('..')).toContainText(body.stats.max_mm.toFixed(1))
  await expect(summary.getByText('Valid cells').locator('..')).toContainText(String(body.stats.n_valid))
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

test('switching to AIFS loads genuine AIFS tiles, metadata and statistics', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  await page.goto('/')
  await waitForTiles(page, /end_step=24/)
  const meta = page.waitForResponse((r) => r.url().includes('/api/forecast/latest?model=aifs-single') && r.status() === 200)
  const tile = page.waitForResponse((r) => isAifsTile(r) && r.status() === 200, { timeout: 30_000 })
  const sum = page.waitForResponse((r) => /\/api\/forecast\/aifs-single\/\d{10}\/summary/.test(r.url()) && r.status() === 200)
  await page.getByRole('radio', { name: /AIFS/ }).click()
  const m = await (await meta).json()
  expect((await tile).headers()['content-type']).toBe('image/png')
  expect(m.model).toBe('aifs-single')
  expect(m.steps.every((s: number) => s % 6 === 0)).toBe(true)          // AIFS is 6-hourly
  await expect(page.getByRole('radio', { name: /AIFS/ })).toHaveAttribute('aria-checked', 'true')
  await expect(page.getByText('ECMWF AIFS forecast · model grid values, not observed rainfall')).toBeVisible()
  await expect(page.getByText(new RegExp(`^Run .*${new Date(m.run).getUTCHours().toString().padStart(2, '0')}:00 UTC`))).toBeVisible()
  const body = await (await sum).json()
  const summary = page.locator('section', { has: page.getByRole('heading', { name: 'Regional summary' }) })
  await expect(summary.getByText('Mean (area-wtd)').locator('..')).toContainText(body.stats.mean_mm.toFixed(2))
  await expect(summary.getByText('Max cell').locator('..')).toContainText(body.stats.max_mm.toFixed(1))
  await page.screenshot({ path: 'e2e/screenshots/desktop-aifs.png' })
  // and back to IFS
  const back = waitForTiles(page, /end_step=/)
  await page.getByRole('radio', { name: /IFS HRES/ }).click()
  await back
  await expect(page.getByText('ECMWF IFS forecast · model grid values, not observed rainfall')).toBeVisible()
  expect(errors).toEqual([])
})
