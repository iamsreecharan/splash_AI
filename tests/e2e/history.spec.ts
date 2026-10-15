import { readFile } from 'node:fs/promises';
import { test, expect } from '@playwright/test';

test('find an older analysis and download its source evidence', async ({ page, request }) => {
  const seeded = await request.post('/api/demo/seed');
  expect(seeded.ok()).toBe(true);
  const { repository_id } = await seeded.json();
  const queued = await request.post('/api/demo/analyze', { data: { scenario: 'rename' } });
  expect(queued.status()).toBe(202);
  const { job_id } = await queued.json();
  await expect.poll(async () => {
    const result = await request.get(`/api/runs/${job_id}`);
    return result.ok() ? (await result.json()).status : 'waiting';
  }, { timeout: 45_000 }).toBe('completed');

  await page.goto('/');
  await page.getByRole('button', { name: 'Analysis History', exact: true }).click();
  await page.getByRole('combobox', { name: 'Filter history project' }).selectOption(repository_id);
  await page.getByRole('combobox', { name: 'Filter history status' }).selectOption('completed');
  await page.getByRole('textbox', { name: 'Search analysis history' }).fill('42');
  const result = page.getByRole('button', { name: /Rename temperature API field/ }).first();
  await expect(result).toBeVisible();
  await expect(page.getByRole('button', { name: /Add a harmless optional field/ })).toHaveCount(0);
  await result.click();
  await expect(page.getByRole('dialog', { name: 'Analysis details' })).toBeVisible();

  const downloadPromise = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download report', exact: true }).click();
  const download = await downloadPromise;
  expect(download.suggestedFilename()).toMatch(/\.md$/);
  const path = await download.path();
  expect(path).toBeTruthy();
  const report = await readFile(path!, 'utf8');
  expect(report).toContain(job_id);
  expect(report).toContain('machine.temperature');
  expect(report).toContain('MachineDashboard.tsx');
  expect(report).toContain('demo-');

  await page.getByRole('button', { name: 'Close dialog' }).click();
  await page.getByRole('textbox', { name: 'Search analysis history' }).fill('not-a-real-pr-94825');
  await expect(page.getByText(/^0 runs(?: found)?$/).first()).toBeVisible();
  await expect(page.getByRole('button', { name: /Rename temperature API field/ })).toHaveCount(0);
});

test('history pages can be browsed on a phone', async ({ page, request }) => {
  test.setTimeout(90_000);
  expect((await request.post('/api/demo/seed')).ok()).toBe(true);
  const current = await (await request.get('/api/runs?limit=1')).json();
  const needed = Math.max(0, 21 - current.total);
  const ids = await Promise.all(Array.from({ length: needed }, async () => {
    const response = await request.post('/api/demo/analyze', { data: { scenario: 'safe' } });
    expect(response.status()).toBe(202);
    return (await response.json()).job_id as string;
  }));
  if (ids.length) {
    await expect.poll(async () => {
      const statuses = await Promise.all(ids.map(async (id) =>
        (await (await request.get(`/api/runs/${id}`)).json()).status));
      return statuses.every((status) => status === 'completed');
    }, { timeout: 45_000 }).toBe(true);
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.getByRole('button', { name: 'Open navigation', exact: true }).click();
  await page.getByRole('button', { name: 'Analysis History', exact: true }).click();
  await expect(page.getByText(/^1–20 of \d+ runs$/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Previous page', exact: true })).toBeDisabled();
  await page.getByRole('button', { name: 'Next page', exact: true }).click();
  await expect(page.getByText(/^21–\d+ of \d+ runs$/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Previous page', exact: true })).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth + 1)).toBe(true);
  await page.getByRole('button', { name: 'Previous page', exact: true }).click();
  await expect(page.getByText(/^1–20 of \d+ runs$/)).toBeVisible();
});
