// QA-053: render the main pages in the LIGHT and DARK theme, run axe's colour-contrast rule on each, take screenshots.
// Usage: BASE=http://127.0.0.1:18084 NODE_PATH=qa/tools/node_modules SHOTS_DIR=<dir> OUT_JSON=<file> node qa/tools/theme_check.cjs
// Fake data / throw-away server only.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const BASE = process.env.BASE || 'http://127.0.0.1:18084';
const SHOTS = path.resolve(process.env.SHOTS_DIR || 'qa/screenshots_after');
const OUT = path.resolve(process.env.OUT_JSON || 'qa/evidence/theme_check.json');
const ONLY = (process.env.ONLY || '').split(',').filter(Boolean);
const axeSrc = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');
fs.mkdirSync(SHOTS, { recursive: true });
fs.mkdirSync(path.dirname(OUT), { recursive: true });

const PAGES = [
  ['dashboard', '/'], ['shipments', '/shipments/'], ['shipment_detail', '/shipments/{shipment}/'], ['shipment_form', '/shipments/create/'],
  ['invoices', '/invoices/'], ['invoice_detail', '/invoices/{invoice}/'], ['invoice_create', '/invoices/create/?customer=1&start_date=2026-01-01&end_date=2026-12-31'],
  ['customers', '/customers/'], ['customer_detail', '/customers/1/'], ['drivers', '/drivers/'], ['driver_form', '/drivers/create/'],
  ['vehicles', '/vehicles/'], ['vehicle_detail', '/vehicles/{vehicle}/'], ['vehicle_form', '/vehicles/create/'],
  ['expenses', '/expenses/'], ['maintenance', '/vehicles/maintenance/'], ['ifta_logs', '/vehicles/ifta/logs/'], ['ifta_report', '/vehicles/ifta/report/?year=2026&month=1'],
  ['monthly', '/reports/financial/monthly/?year=2026&month=1'], ['yearly', '/reports/financial/yearly/?year=2026'],
  ['settings', '/settings/system/'], ['whatsapp', '/shipments/settings/whatsapp/'], ['import', '/settings/import/'],
];

async function login(page) {
  await page.goto(BASE + '/auth/login/');
  await page.fill('input[name=username]', 'qa_admin');
  await page.fill('input[name=password]', 'qa-pass-123');
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit], input[type=submit]')]);
}

(async () => {
  const browser = await chromium.launch();
  const R = { pages: [] };
  for (const width of [1440, 375]) {
    for (const theme of ['light', 'dark']) {
      const ctx = await browser.newContext({ viewport: { width, height: 900 } });
      await ctx.addInitScript((t) => { try { localStorage.setItem('fila-theme', t); } catch (e) {} }, theme);
      const page = await ctx.newPage();
      const errors = [];
      page.on('pageerror', (e) => errors.push(String(e).slice(0, 160)));
      await login(page);
      // ids of real rows for the detail pages
      const ids = {};
      for (const [key, url, re] of [['shipment', '/shipments/', /^\/shipments\/(\d+)\/$/], ['invoice', '/invoices/', /^\/invoices\/(\d+)\/$/], ['vehicle', '/vehicles/', /^\/vehicles\/(\d+)\/$/]]) {
        await page.goto(BASE + url);
        const hrefs = await page.locator('a').evaluateAll((els) => els.map((e) => e.getAttribute('href')));
        const m = hrefs.map((h) => (h || '').match(re)).find(Boolean);
        ids[key] = m ? m[1] : '1';
      }
      for (const [name, tpl] of PAGES) {
        if (ONLY.length && !ONLY.includes(name)) continue;
        const url = tpl.replace('{shipment}', ids.shipment).replace('{invoice}', ids.invoice).replace('{vehicle}', ids.vehicle);
        const resp = await page.goto(BASE + url, { waitUntil: 'networkidle' });
        const attr = await page.evaluate(() => document.documentElement.getAttribute('data-theme'));
        await page.evaluate(axeSrc);
        const res = await page.evaluate(async () => {
          const r = await axe.run(document, { runOnly: ['color-contrast'], resultTypes: ['violations'] });
          const nodes = r.violations.flatMap((v) => v.nodes.map((n) => ({ target: n.target.join(' ').slice(0, 90), summary: ((n.any[0] || {}).message || '').slice(0, 140) })));
          return { count: nodes.length, nodes: nodes.slice(0, 6) };
        });
        const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);
        if (width === 1440 || name === 'dashboard' || name === 'shipments') {
          await page.screenshot({ path: path.join(SHOTS, `qa053_${theme}_${name}_${width}.png`), fullPage: true });
        }
        const rec = { width, theme, page: name, status: resp.status(), dataTheme: attr, contrastViolations: res.count, overflow, sample: res.nodes };
        R.pages.push(rec);
        console.log(width, theme, name, resp.status(), 'contrast:', res.count, overflow ? 'OVERFLOW' : '');
      }
      R.consoleErrors = (R.consoleErrors || []).concat(errors);
      await ctx.close();
    }
  }
  fs.writeFileSync(OUT, JSON.stringify(R, null, 2));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
