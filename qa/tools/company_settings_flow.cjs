// QA-052: edit the company information in System Settings and see it on the invoice (fake data, throw-away server).
// Usage: BASE=http://127.0.0.1:18083 NODE_PATH=qa/tools/node_modules SHOTS_DIR=qa/screenshots_after OUT_JSON=qa/evidence/company_settings_flow.json node qa/tools/company_settings_flow.cjs
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const BASE = process.env.BASE || 'http://127.0.0.1:18083';
const SHOTS = path.resolve(process.env.SHOTS_DIR || 'qa/screenshots_after');
const OUT = path.resolve(process.env.OUT_JSON || 'qa/evidence/company_settings_flow.json');
fs.mkdirSync(SHOTS, { recursive: true });
fs.mkdirSync(path.dirname(OUT), { recursive: true });
const R = { steps: [] };
const rec = (s, v) => { R.steps.push({ step: s, ...v }); console.log(s, '=>', JSON.stringify(v)); };
async function login(page) {
  await page.goto(BASE + '/auth/login/');
  await page.fill('input[name=username]', 'qa_admin');
  await page.fill('input[name=password]', 'qa-pass-123');
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit], input[type=submit]')]);
}
(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
  await login(page);
  await page.goto(BASE + '/settings/system/', { waitUntil: 'networkidle' });
  await page.screenshot({ path: path.join(SHOTS, 'qa052_settings_1440.png'), fullPage: true });
  await page.fill('#id_company_name', 'Acme Hauling LLC');
  await page.fill('#id_company_address', '500 Harbor Road\nSuite 7\nGary, IN 46402');
  await page.fill('#id_company_phone', '(219) 555-0142');
  await page.fill('#id_company_email', 'billing@acme-hauling.example');
  await Promise.all([page.waitForNavigation(), page.click('button:has-text("Save All Changes")')]);
  const flash = (await page.locator('#flash-messages').innerText().catch(() => '')).replace(/\s+/g, ' ').trim();
  rec('saved', { flash });
  await page.goto(BASE + '/invoices/', { waitUntil: 'networkidle' });
  const href = await page.locator('a[href^="/invoices/"][href$="/"]').filter({ hasNot: page.locator('text=Create') }).evaluateAll((els) => els.map((e) => e.getAttribute('href')).find((h) => /^\/invoices\/\d+\/$/.test(h)));
  await page.goto(BASE + href, { waitUntil: 'networkidle' });
  const text = (await page.locator('#invoice-canvas').innerText()).replace(/\s+/g, ' ');
  await page.screenshot({ path: path.join(SHOTS, 'qa052_invoice_1440.png'), fullPage: true });
  rec('invoice preview', { company: /Acme Hauling LLC/i.test(text), address: /500 Harbor Road/i.test(text) && /Gary, IN 46402/i.test(text), phone: /219\) 555-0142/.test(text), email: /billing@acme-hauling.example/i.test(text), oldAddressGone: !/123 Fleet Street/i.test(text) });
  const phone = await (await browser.newContext({ viewport: { width: 375, height: 800 } })).newPage();
  await login(phone);
  const ov = {};
  for (const [n, u] of [['settings', '/settings/system/'], ['invoice', href]]) {
    await phone.goto(BASE + u, { waitUntil: 'networkidle' });
    await phone.screenshot({ path: path.join(SHOTS, `qa052_${n}_375.png`), fullPage: true });
    ov[n] = await phone.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  }
  rec('phone 375 horizontal overflow', ov);
  R.consoleErrors = errors;
  fs.writeFileSync(OUT, JSON.stringify(R, null, 2));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
