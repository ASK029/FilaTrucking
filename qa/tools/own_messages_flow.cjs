// QA-054: the "accept shipments typed from the connected WhatsApp account" checkbox (fake data, throw-away server).
// Usage: BASE=http://127.0.0.1:18085 NODE_PATH=qa/tools/node_modules SHOTS_DIR=qa/screenshots_after OUT_JSON=qa/evidence/own_messages_flow.json node qa/tools/own_messages_flow.cjs
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const BASE = process.env.BASE || 'http://127.0.0.1:18085';
const SHOTS = path.resolve(process.env.SHOTS_DIR || 'qa/screenshots_after');
const OUT = path.resolve(process.env.OUT_JSON || 'qa/evidence/own_messages_flow.json');
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
const URL = '/shipments/settings/whatsapp/';
const BOX = '#accept-own-messages';
// what the sidecar would read (it sends the bearer secret; here the browser session is enough)
const apiFlag = (page) => page.evaluate(async () => (await (await fetch('/shipments/api/whatsapp/groups/', { credentials: 'same-origin' })).json()).accept_own_messages);

(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1440, height: 900 } })).newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
  page.on('dialog', (d) => { R.lastDialog = d.message(); d.dismiss(); });
  await login(page);

  await page.goto(BASE + URL, { waitUntil: 'networkidle' });
  rec('initial', { present: await page.locator(BOX).count() === 1, checked: await page.locator(BOX).isChecked(), api: await apiFlag(page) });
  await page.screenshot({ path: path.join(SHOTS, 'qa054_settings_off_1440.png'), fullPage: true });

  await Promise.all([page.waitForResponse((r) => r.url().includes('/whatsapp/options/')), page.locator(BOX).click()]);
  rec('ticked', { checked: await page.locator(BOX).isChecked(), api: await apiFlag(page) });
  await page.reload({ waitUntil: 'networkidle' });
  rec('after reload', { checked: await page.locator(BOX).isChecked() });
  await page.screenshot({ path: path.join(SHOTS, 'qa054_settings_on_1440.png'), fullPage: true });

  // a failing save puts the box back
  const errorsBefore = errors.length;
  await page.route('**/whatsapp/options/', (route) => route.fulfill({ status: 500, body: '{}' }));
  await page.locator(BOX).click();
  await page.waitForTimeout(400);
  rec('failed save reverts the box', { checked: await page.locator(BOX).isChecked(), alerted: /Could not save/.test(R.lastDialog || ''), api: await apiFlag(page) });
  await page.unroute('**/whatsapp/options/');
  R.expectedErrorsFromTheSimulated500 = errors.splice(errorsBefore); // the simulated failure logs its own console errors

  await Promise.all([page.waitForResponse((r) => r.url().includes('/whatsapp/options/')), page.locator(BOX).click()]);
  rec('unticked', { checked: await page.locator(BOX).isChecked(), api: await apiFlag(page) });

  // light theme + phone width
  const phone = await (await browser.newContext({ viewport: { width: 375, height: 800 } })).newPage();
  phone.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));
  phone.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
  await login(phone);
  const ov = {};
  for (const theme of ['dark', 'light']) {
    await phone.evaluate((t) => { try { localStorage.setItem('fila-theme', t); } catch (e) {} }, theme);
    await phone.goto(BASE + URL, { waitUntil: 'networkidle' });
    await phone.screenshot({ path: path.join(SHOTS, `qa054_settings_${theme}_375.png`), fullPage: true });
    ov[theme] = await phone.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
  }
  rec('phone 375 horizontal overflow', ov);
  R.consoleErrors = errors;
  fs.writeFileSync(OUT, JSON.stringify(R, null, 2));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
