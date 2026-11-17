// QA-051: vehicle documents, shipment chassis, invoice description/location defaults and the new WhatsApp message, in a real browser.
// Usage: BASE=http://127.0.0.1:18082 SECRET=<ingest secret of the throw-away server> SHOTS_DIR=qa/screenshots_after OUT_JSON=qa/evidence/vehicle_shipment_flow.json \
//        NODE_PATH=qa/tools/node_modules node qa/tools/vehicle_shipment_flow.cjs
// Fake data and a throw-away SQLite server only: the message goes to Django's ingest endpoint, no WhatsApp message is ever sent.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const os = require('os');
const BASE = process.env.BASE || 'http://127.0.0.1:18082';
const SECRET = process.env.SECRET || '';
const SHOTS = path.resolve(process.env.SHOTS_DIR || 'qa/screenshots_after');
const OUT = path.resolve(process.env.OUT_JSON || 'qa/evidence/vehicle_shipment_flow.json');
const CUSTOMER_LABEL = process.env.CUSTOMER_LABEL || 'Acme Shipping Line';
const CUSTOMER_ABBR = process.env.CUSTOMER_ABBR || 'ASL';
fs.mkdirSync(SHOTS, { recursive: true });
fs.mkdirSync(path.dirname(OUT), { recursive: true });
const REG = 'T-Q' + (Date.now() % 100000);
const day = (offset) => new Date(Date.now() + offset * 86400000).toISOString().slice(0, 10);
const tmp = fs.mkdtempSync(path.join(os.tmpdir(), 'qa051-'));
const pdf = (name) => { const p = path.join(tmp, name); fs.writeFileSync(p, '%PDF-1.4 fake'); return p; };
const R = { steps: [] };
const rec = (step, v) => { R.steps.push({ step, ...v }); console.log(step, '=>', JSON.stringify(v)); };
const shot = (page, name) => page.screenshot({ path: path.join(SHOTS, `qa051_${name}.png`), fullPage: true });
const overflow = (page) => page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth);

async function login(page) {
  await page.goto(BASE + '/auth/login/');
  await page.fill('input[name=username]', 'qa_admin');
  await page.fill('input[name=password]', 'qa-pass-123');
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit], input[type=submit]')]);
}

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e).slice(0, 200)));
  page.on('console', (m) => { if (m.type() === 'error') errors.push(m.text().slice(0, 200)); });
  await login(page);

  // ---- A. vehicle with two documents -------------------------------------------------------------------------
  await page.goto(BASE + '/vehicles/create/', { waitUntil: 'networkidle' });
  await shot(page, 'vehicle_form_1440');
  await page.fill('#id_registration_number', REG);
  await page.fill('#id_model', '579');
  await page.fill('#id_chassis_number', '1XKYD49X0LJ0QA051');
  await page.fill('input[name="documents-0-name"]', 'Registration');
  await page.setInputFiles('input[name="documents-0-document"]', pdf('registration.pdf'));
  await page.click('#add-document-form');
  await page.fill('input[name="documents-1-name"]', 'Insurance');
  await page.setInputFiles('input[name="documents-1-document"]', pdf('insurance.pdf'));
  await Promise.all([page.waitForNavigation(), page.click('button:has-text("Add Vehicle")')]);
  const href = await page.locator(`tr:has-text("${REG}") a[href$="/"]`).first().getAttribute('href');
  const vehicleUrl = BASE + href;
  await page.goto(vehicleUrl, { waitUntil: 'networkidle' });
  let docs = await page.locator('a:has-text("Registration"), a:has-text("Insurance")').count();
  await shot(page, 'vehicle_detail_1440');
  rec('vehicle created with 2 documents', { documentLinks: docs, url: vehicleUrl.replace(BASE, '') });

  // edit: the existing rows are shown, one is removed, one is added
  await page.goto(vehicleUrl + 'edit/', { waitUntil: 'networkidle' });
  await shot(page, 'vehicle_edit_1440');
  const rows = await page.locator('.document-form-row').count();
  await page.locator('input[name="documents-0-DELETE"]').check();
  await page.click('#add-document-form');
  const newIndex = (await page.locator('input[name$="-name"]').count()) - 1;
  await page.fill(`input[name="documents-${newIndex}-name"]`, 'Inspection');
  await page.setInputFiles(`input[name="documents-${newIndex}-document"]`, pdf('inspection.pdf'));
  await Promise.all([page.waitForNavigation(), page.click('button:has-text("Save Changes")')]);
  await page.goto(vehicleUrl, { waitUntil: 'networkidle' });
  const names = await page.locator('main li a, li a').allInnerTexts();
  rec('vehicle edit (remove 1, add 1)', { rowsShownOnEdit: rows, documentNames: names.filter((n) => /Registration|Insurance|Inspection/.test(n)) });

  // delete link -> confirm page
  await page.click('a:has-text("Delete") >> nth=0');
  await Promise.all([page.waitForNavigation(), page.click('button:has-text("Yes, delete")')]);
  rec('document delete', { backOnDetail: page.url() === vehicleUrl, remaining: await page.locator('li a:has-text("Inspection"), li a:has-text("Insurance"), li a:has-text("Registration")').count() });

  // ---- B. shipment with chassis, blank location ------------------------------------------------------------------
  await page.goto(BASE + '/shipments/create/', { waitUntil: 'networkidle' });
  await shot(page, 'shipment_form_1440');
  await page.fill('#id_date', day(0));
  await page.fill('#id_booking', 'QA51-BK');
  await page.fill('#id_container', 'HAMU3898114');
  await page.fill('#id_seal', '179458');
  await page.fill('#id_chassis', 'NAPZ225644');
  await page.selectOption('#id_customer', { label: CUSTOMER_LABEL });
  await page.selectOption('#id_status', 'confirmed');
  await Promise.all([page.waitForNavigation(), page.click('button:has-text("Add Shipment")')]);
  await page.goto(BASE + (await page.locator('tr:has-text("HAMU3898114") a[href$="/"]').first().getAttribute('href')), { waitUntil: 'networkidle' });
  const detailText = (await page.locator('main').innerText()).replace(/\s+/g, ' ');
  await shot(page, 'shipment_detail_1440');
  rec('shipment with chassis', { url: page.url().replace(BASE, ''), showsChassis: /NAPZ225644/.test(detailText), locationIsCustomerAbbreviation: new RegExp('Location\\s+' + CUSTOMER_ABBR, 'i').test(detailText) });

  // ---- C. invoice screen: empty description, location = abbreviation -----------------------------------------------
  await page.goto(`${BASE}/invoices/create/?customer=1&start_date=${day(-3)}&end_date=${day(3)}`, { waitUntil: 'networkidle' });
  const line = await page.evaluate(() => {
    const rows = [...document.querySelectorAll('input[name$="-container_no"]')];
    const i = rows.findIndex((el) => el.value === 'HAMU3898114');
    if (i < 0) return null;
    const get = (n) => (document.querySelector(`input[name$="-${i}-${n}"]`) || {}).value;
    return { description: get('description'), location: get('location'), booking: get('booking_no') };
  });
  await shot(page, 'invoice_create_1440');
  rec('invoice line prefill', { line, descriptionEmpty: line && line.description === '', locationIsAbbreviation: line && line.location === CUSTOMER_ABBR });

  // ---- D. the new WhatsApp message (Django endpoint only) -------------------------------------------------------
  const send = (text, extra = {}) => page.evaluate(async ({ base, secret, text, extra }) => {
    const r = await fetch(base + '/shipments/api/ingest/', { method: 'POST', headers: { 'Content-Type': 'application/json', Authorization: 'Bearer ' + secret }, body: JSON.stringify({ text, sender: '000@lid', ...extra }) });
    return r.json();
  }, { base: BASE, secret: SECRET, text, extra });
  const ex = await send('Klaipeda\n95938662\nHAMU3898999\n179458\nNAPZ225644', { message_id: `QA51-${REG}-1` });
  const chat = await send('ok thanks boss', { message_id: `QA51-${REG}-2` });
  const miss = await send('Klaipeda\n95938662\nHAMU3898998', { message_id: `QA51-${REG}-3` });
  rec('whatsapp', { example: ex.status, exampleReply: ex.reply_message, chat: chat.status, chatHasReply: 'reply_message' in chat, missingSeal: miss.status, missingReply: (miss.reply_message || '').split('\n')[0] });
  await page.goto(BASE + '/shipments/', { waitUntil: 'networkidle' });
  const listText = (await page.locator('main').innerText()).replace(/\s+/g, ' ');
  await page.goto(BASE + (await page.locator('tr:has-text("HAMU3898999") a[href$="/"]').first().getAttribute('href')), { waitUntil: 'networkidle' });
  const wa = (await page.locator('main').innerText()).replace(/\s+/g, ' ');
  await shot(page, 'shipment_whatsapp_1440');
  rec('whatsapp shipment page', { listShowsChassis: /NAPZ225644/.test(listText), note: /Klaipeda/.test(wa), unassigned: /Unassigned|UNASSIGNED/.test(wa), flagReasonShown: /Why it is flagged/i.test(wa) && /set the customer/.test(wa), noAutoIngested: !/Auto-ingested/.test(wa) });

  // ---- E. phone width -----------------------------------------------------------------------------------------------
  const phone = await browser.newContext({ viewport: { width: 375, height: 800 } });
  const mp = await phone.newPage();
  await login(mp);
  const ov = {};
  for (const [name, url] of [['vehicle_form', BASE + '/vehicles/create/'], ['vehicle_detail', vehicleUrl], ['vehicle_edit', vehicleUrl + 'edit/'], ['shipment_form', BASE + '/shipments/create/'], ['shipment_list', BASE + '/shipments/']]) {
    await mp.goto(url, { waitUntil: 'networkidle' });
    await shot(mp, `${name}_375`);
    ov[name] = await overflow(mp);
  }
  rec('phone 375 horizontal overflow', ov);

  R.consoleErrors = errors;
  fs.writeFileSync(OUT, JSON.stringify(R, null, 2));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
