import { test, expect } from '@playwright/test';

test('sample PR produces grounded evidence and feedback persists', async ({ page, request }) => {
  const config = await request.get('/api/public/config');
  expect(config.ok()).toBeTruthy();
  expect((await config.json()).demo_mode).toBe(true);
  const seeded = await request.post('/api/demo/seed');
  expect(seeded.ok()).toBeTruthy();
  const analyzed = await request.post('/api/demo/analyze', { data: { scenario: 'rename' } });
  expect(analyzed.status()).toBe(202);
  const { job_id } = await analyzed.json();
  await expect.poll(async () => {
    const response = await request.get(`/api/runs/${job_id}`);
    return response.ok() ? (await response.json()).status : 'waiting';
  }, { timeout: 45_000 }).toBe('completed');

  const run = await (await request.get(`/api/runs/${job_id}`)).json();
  const response = await request.get('/api/alerts');
  const alert = (await response.json()).items.find((item: any) => item.pr_number === run.pr_number);
  expect(alert).toBeTruthy();
  expect(alert.evidence_status).toBe('verified');
  expect(alert.evidence.some((item: any) => item.path.includes('MachineDashboard'))).toBe(true);
  await page.goto('/');
  await page.getByRole('button', { name: 'Impact Center', exact: true }).click();
  await page.getByText(alert.title, { exact: true }).first().click();
  await expect(page.getByText('machine.temperature', { exact: false }).first()).toBeVisible();
  await page.getByRole('button', { name: 'Mark useful', exact: true }).click();
  await expect.poll(async () => (await (await request.get(`/api/alerts/${alert.id}`)).json()).feedback).toBe('useful');
  await page.getByRole('button', { name: 'Mark resolved', exact: true }).click();
  await expect.poll(async () => (await (await request.get(`/api/alerts/${alert.id}`)).json()).status).toBe('resolved');
  await page.reload();
  const persisted = await request.get(`/api/alerts/${alert.id}`);
  expect((await persisted.json()).status).toBe('resolved');
});

test('harmless sample change completes without alerts', async ({ request }) => {
  const response = await request.post('/api/demo/analyze', { data: { scenario: 'safe' } });
  expect(response.status()).toBe(202);
  const { job_id } = await response.json();
  await expect.poll(async () => (await (await request.get(`/api/runs/${job_id}`)).json()).status,
    { timeout: 45_000 }).toBe('completed');
  const run = await (await request.get(`/api/runs/${job_id}`)).json();
  expect(run.alert_count).toBe(0);
  expect(run.stages).toHaveLength(4);
});

test('mobile navigation and settings reflect real configuration', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  const menu = page.getByRole('button', { name: /open navigation|toggle navigation|menu/i });
  await expect(menu).toBeVisible();
  await menu.click();
  await page.getByRole('button', { name: 'Settings', exact: true }).click();
  await expect(page.getByRole('heading', { name: /settings/i }).first()).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
});
