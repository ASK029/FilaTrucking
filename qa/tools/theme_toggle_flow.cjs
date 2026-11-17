// QA-053: click the theme toggle in a real browser: default dark, switch to light, survives a reload and navigation, switch back.
// Usage: BASE=http://127.0.0.1:18084 NODE_PATH=qa/tools/node_modules OUT_JSON=<file> node qa/tools/theme_toggle_flow.cjs (fake data, throw-away server)
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const BASE = process.env.BASE || 'http://127.0.0.1:18084';
const OUT = path.resolve(process.env.OUT_JSON || 'qa/evidence/theme_toggle_flow.json');
const R = { steps: [] };
const rec = (s, v) => { R.steps.push({ step: s, ...v }); console.log(s, '=>', JSON.stringify(v)); };
(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 375, height: 800 } })).newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 160)));
  await page.goto(BASE + '/auth/login/');
  await page.fill('input[name=username]', 'qa_admin'); await page.fill('input[name=password]', 'qa-pass-123');
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit]')]);
  const theme = () => page.evaluate(() => document.documentElement.getAttribute('data-theme'));
  const bg = () => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
  const label = () => page.locator('[data-theme-toggle]').first().getAttribute('aria-label');
  rec('first visit (nothing saved)', { theme: await theme(), bg: await bg(), label: await label() });
  await page.locator('[data-theme-toggle]').first().click();
  rec('after one click', { theme: await theme(), bg: await bg(), label: await label() });
  await page.reload({ waitUntil: 'networkidle' });
  rec('after reload', { theme: await theme(), bg: await bg() });
  await page.goto(BASE + '/vehicles/', { waitUntil: 'networkidle' });
  rec('other page', { theme: await theme() });
  await page.locator('[data-theme-toggle]').first().click();
  rec('clicked again', { theme: await theme(), bg: await bg() });
  const box = await page.locator('[data-theme-toggle]').first().boundingBox();
  rec('tap target', { width: Math.round(box.width), height: Math.round(box.height) });
  R.consoleErrors = errors;
  fs.mkdirSync(path.dirname(OUT), { recursive: true });
  fs.writeFileSync(OUT, JSON.stringify(R, null, 2));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
