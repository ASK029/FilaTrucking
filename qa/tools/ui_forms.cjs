// QA interactive UI checks (forms, XSS, JS behaviour, mobile nav, keyboard). Output -> qa/evidence/ui_forms.json
// Usage: BASE=http://127.0.0.1:18080 node qa/tools/ui_forms.cjs
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const BASE = process.env.BASE || 'http://127.0.0.1:18080';
const ROOT = path.resolve(__dirname, '..');
const SHOTS = process.env.SHOTS_DIR ? path.resolve(process.env.SHOTS_DIR) : path.join(ROOT, 'screenshots');
const OUT = process.env.OUT_JSON ? path.resolve(process.env.OUT_JSON) : path.join(ROOT, 'evidence', 'ui_forms.json');
require('fs').mkdirSync(SHOTS, { recursive: true });
const R = {};
const rec = (k, v) => { R[k] = v; console.log(k, '=>', JSON.stringify(v)); };

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const dialogs = [];
  page.on('dialog', async (d) => { dialogs.push(d.message()); await d.dismiss(); });
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));

  // 0. anonymous access + wrong password
  await page.goto(BASE + '/customers/');
  rec('anon_redirect_url', page.url().replace(BASE, ''));
  await page.fill('input[name=username]', 'qa_admin'); await page.fill('input[name=password]', 'wrong');
  await page.click('button[type=submit], input[type=submit]'); await page.waitForLoadState('networkidle');
  rec('wrong_password_feedback', (await page.locator('body').innerText()).replace(/\s+/g, ' ').match(/(please enter|incorrect|invalid|didn.t match)[^.]*\./i)?.[0] || 'NO VISIBLE ERROR TEXT');
  await page.fill('input[name=username]', 'qa_admin'); await page.fill('input[name=password]', 'qa-pass-123');
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit], input[type=submit]')]);

  // 1. Customer form: empty submit, invalid email, required-attribute usage
  await page.goto(BASE + '/customers/create/', { waitUntil: 'networkidle' });
  const noValidate = await page.locator('form:not([action$="/auth/logout/"])').first().getAttribute('novalidate');
  await page.locator('form:not([action$="/auth/logout/"]) button[type=submit], form:not([action$="/auth/logout/"]) input[type=submit]').first().click();
  await page.waitForTimeout(500);
  rec('customer_empty_submit', { stayedOnForm: page.url().includes('/customers/create/'), novalidateAttr: noValidate, visibleErrors: await page.locator('.errorlist, [class*=text-red]').count() });
  await page.screenshot({ path: path.join(SHOTS, 'form_customer_empty_submit_1440.png'), fullPage: true });

  // 2. XSS rendering in lists (seeded name contains <script>)
  await page.goto(BASE + '/customers/', { waitUntil: 'networkidle' });
  rec('xss_customer_name_executed', await page.evaluate(() => !!window.__xss_customer));
  await page.goto(BASE + '/shipments/', { waitUntil: 'networkidle' });
  rec('xss_in_shipment_list_executed', await page.evaluate(() => !!window.__xss_location || !!window.__xss_customer));

  // 3. Stored XSS via shipment data embedded with |safe inside <script> on the invoice "lines" page
  await page.goto(BASE + '/invoices/1/lines/', { waitUntil: 'networkidle' });
  rec('xss_invoice_lines_page_executed', await page.evaluate(() => !!window.__xss_location));
  await page.screenshot({ path: path.join(SHOTS, 'xss_invoice_lines_1440.png'), fullPage: false });

  // 4. Invoice create: "Add Manual Item" JS
  errors.length = 0;
  await page.goto(BASE + '/invoices/create/', { waitUntil: 'networkidle' });
  const tid = await page.evaluate(() => ({ jsExpects: 'id_form-TOTAL_FORMS', present: !!document.getElementById('id_form-TOTAL_FORMS'), actualIds: [...document.querySelectorAll('input[id$=TOTAL_FORMS]')].map((e) => e.id) }));
  rec('invoice_form_management_form_ids', tid);
  const addBtn = page.locator('button:has-text("Add Manual Item")').first();
  if (await addBtn.count()) { await addBtn.click(); await page.waitForTimeout(300); }
  rec('invoice_add_manual_item_js_errors', errors.slice());
  const names = await page.evaluate(() => [...document.querySelectorAll('#line-items-body input')].map((i) => i.name).slice(0, 4));
  rec('invoice_manual_row_input_names', names);

  // 5. Shipment create: double submit
  await page.goto(BASE + '/shipments/create/', { waitUntil: 'networkidle' });
  const before = await (await ctx.request.get(BASE + '/shipments/')).text();
  const countRows = (html) => (html.match(/QADOUBLE\d+/g) || []).length;
  await page.selectOption('select[name=customer]', { index: 1 });
  await page.fill('input[name=date]', '2026-11-01');
  await page.fill('input[name=booking]', 'QADOUBLE1'); await page.fill('input[name=container]', 'QADOUBLE-C1');
  await page.fill('input[name=seal]', '555'); await page.fill('input[name=location]', 'Chicago');
  const submit = page.locator('form:not([action$="/auth/logout/"]) button[type=submit]').first();
  const disabledAfterClick = await (async () => { await submit.click({ noWaitAfter: true }); try { await submit.click({ noWaitAfter: true, timeout: 800 }); } catch (e) {} return null; })();
  await page.waitForLoadState('networkidle');
  const after = await page.evaluate(async () => (await (await fetch('/shipments/')).text()));
  rec('shipment_double_click_submit_rows_created', (after.match(/QADOUBLE-C1/g) || []).length - (before.match(/QADOUBLE-C1/g) || []).length);

  // 6. WhatsApp settings page: QR, status polling, console
  errors.length = 0;
  const reqs = [];
  page.on('request', (r) => { if (r.url().includes('/api/whatsapp/')) reqs.push(r.url().replace(BASE, '')); });
  await page.goto(BASE + '/shipments/settings/whatsapp/', { waitUntil: 'networkidle' });
  await page.waitForTimeout(6000);
  rec('whatsapp_settings', { qrCanvasOrImg: await page.locator('#qr-code canvas, #qrcode canvas, canvas, img[alt*=QR i]').count(), apiCallsIn6s: reqs, jsErrors: errors.slice(),
    pollsEvery: 'see setInterval in whatsapp_settings.html:357', staleErrorShown: /xxxxxxxx/.test(await page.locator('body').innerText()) });
  await page.screenshot({ path: path.join(SHOTS, 'whatsapp_settings_state_1440.png'), fullPage: true });

  // 7. Keyboard: tab through the dashboard and check a visible focus indicator
  await page.goto(BASE + '/', { waitUntil: 'networkidle' });
  const focus = [];
  for (let i = 0; i < 14; i++) {
    await page.keyboard.press('Tab');
    focus.push(await page.evaluate(() => { const e = document.activeElement; if (!e || e === document.body) return null; const s = getComputedStyle(e); return { tag: e.tagName, text: (e.innerText || e.getAttribute('aria-label') || '').trim().slice(0, 25), outline: s.outlineStyle !== 'none' && parseFloat(s.outlineWidth) > 0, ring: s.boxShadow !== 'none' }; }));
  }
  const withIndicator = focus.filter((f) => f && (f.outline || f.ring)).length;
  rec('keyboard_focus_indicator', { tabbed: focus.filter(Boolean).length, withVisibleIndicator: withIndicator, firstStops: focus.slice(0, 4) });
  const skip = await page.evaluate(() => !!document.querySelector('a[href^="#main"], a.sr-only, a[href="#content"]'));
  rec('skip_to_content_link', skip);

  // 8. Mobile navigation
  const m = await browser.newContext({ viewport: { width: 375, height: 812 } });
  const mp = await m.newPage();
  await mp.goto(BASE + '/auth/login/'); await mp.fill('input[name=username]', 'qa_admin'); await mp.fill('input[name=password]', 'qa-pass-123');
  await Promise.all([mp.waitForNavigation(), mp.click('button[type=submit], input[type=submit]')]);
  await mp.waitForLoadState('networkidle');
  const hamburger = mp.locator('button[aria-label*=menu i], button:has(svg)').first();
  const navBefore = await mp.locator('aside a, nav a').filter({ hasText: 'Shipments' }).first().isVisible().catch(() => false);
  await mp.screenshot({ path: path.join(SHOTS, 'mobile_nav_closed_375.png') });
  if (await hamburger.count()) { await hamburger.click(); await mp.waitForTimeout(500); }
  const navAfter = await mp.locator('aside a, nav a').filter({ hasText: 'Shipments' }).first().isVisible().catch(() => false);
  await mp.screenshot({ path: path.join(SHOTS, 'mobile_nav_open_375.png') });
  rec('mobile_nav', { shipmentsLinkVisibleBeforeOpen: navBefore, afterTappingHamburger: navAfter });
  // session: logout is POST-only in Django >=5
  const lo = await mp.goto(BASE + '/auth/logout/'); rec('logout_via_GET_status', lo.status());
  // the real user action: tap the Logout button (QA-049)
  await mp.goto(BASE + '/', { waitUntil: 'networkidle' });
  await Promise.all([mp.waitForNavigation().catch(() => {}), mp.locator('header form[action$="/auth/logout/"] button').click()]);
  rec('logout_button_click', { urlAfter: mp.url().replace(BASE, ''), signedOut: (await mp.goto(BASE + '/customers/')) && mp.url().includes('/auth/login/') });
  await m.close();

  // 9. 404 page quality
  const nf = await page.goto(BASE + '/does-not-exist/');
  rec('404_page', { status: nf.status(), customTemplate: !/Page not found \(404\)/i.test(await page.content()) });
  await browser.close();
  fs.writeFileSync(OUT, JSON.stringify(R, null, 1));
})().catch((e) => { console.error(e); process.exit(1); });
