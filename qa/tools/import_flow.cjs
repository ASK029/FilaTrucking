// QA-050: Import Data in a real browser: upload -> preview -> confirm -> re-upload (duplicates) at 1440 px, preview at 375 px.
// Usage: BASE=http://127.0.0.1:18081 FILES="a.csv|b.csv|c.xlsx" SHOTS_DIR=qa/screenshots_after OUT_JSON=qa/evidence/import_flow.json \
//        PREFIX=import node qa/tools/import_flow.cjs
// The JSON only records counts, flags and messages. Never point SHOTS_DIR at a tracked folder when FILES are the owner's private statements.
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const BASE = process.env.BASE || 'http://127.0.0.1:18081';
const FILES = (process.env.FILES || '').split('|').filter(Boolean).map((f) => path.resolve(f));
const SHOTS = path.resolve(process.env.SHOTS_DIR || 'qa/screenshots_after');
const OUT = path.resolve(process.env.OUT_JSON || 'qa/evidence/import_flow.json');
const PREFIX = process.env.PREFIX || 'import';
fs.mkdirSync(SHOTS, { recursive: true });
fs.mkdirSync(path.dirname(OUT), { recursive: true });
const R = { files: FILES.map((f) => path.basename(f)), steps: [] };
const rec = (step, v) => { R.steps.push({ step, ...v }); console.log(step, '=>', JSON.stringify(v)); };

async function login(page) {
  await page.goto(BASE + '/auth/login/');
  await page.fill('input[name=username]', 'qa_admin');
  await page.fill('input[name=password]', 'qa-pass-123');
  await Promise.all([page.waitForNavigation(), page.click('button[type=submit], input[type=submit]')]);
}

async function overflow(page) {
  return page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, innerWidth: window.innerWidth }));
}

async function upload(page, file) {
  await page.goto(BASE + '/settings/import/', { waitUntil: 'networkidle' });
  await page.setInputFiles('#csv_file', file);
  await Promise.all([page.waitForLoadState('networkidle'), page.click('button:has-text("Preview Import")')]);
}

async function flash(page) {
  const el = page.locator('#flash-messages');
  return (await el.count()) ? (await el.innerText()).replace(/\s+/g, ' ').trim() : '';
}

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await ctx.newPage();
  const consoleErrors = [];
  page.on('pageerror', (e) => consoleErrors.push(String(e).slice(0, 200)));
  page.on('console', (m) => { if (m.type() === 'error') consoleErrors.push(m.text().slice(0, 200)); });
  await login(page);

  // the form
  await page.goto(BASE + '/settings/import/', { waitUntil: 'networkidle' });
  await page.screenshot({ path: path.join(SHOTS, `${PREFIX}_form_1440.png`), fullPage: true });
  rec('form', { accept: await page.locator('#csv_file').getAttribute('accept'), button: (await page.locator('button[type=submit]').last().innerText()).trim() });

  let n = 0;
  for (const file of FILES) {
    n += 1;
    await upload(page, file);
    const cards = (await page.locator('main .grid').first().innerText().catch(() => '')).replace(/\s+/g, ' ');
    const rows = await page.locator('table.responsive-table tbody tr').count();
    const confirm = page.locator('button:has-text("Confirm import")');
    const hasConfirm = (await confirm.count()) > 0;
    const warnings = await page.locator('h2:has-text("Please check") + ul li').count();
    await page.screenshot({ path: path.join(SHOTS, `${PREFIX}_preview_${n}_1440.png`), fullPage: true });
    rec(`preview ${path.basename(file)}`, { monthRows: rows, hasConfirm, warnings, saved_before_confirm: null, cards: process.env.SHOW_CARDS ? cards : undefined });
    if (hasConfirm) {
      await Promise.all([page.waitForNavigation(), confirm.click()]);
      await page.waitForLoadState('networkidle');
      const msg = await flash(page);
      await page.screenshot({ path: path.join(SHOTS, `${PREFIX}_done_${n}_1440.png`), fullPage: true });
      rec(`confirm ${path.basename(file)}`, { url: page.url().replace(BASE, ''), message: process.env.HIDE_MESSAGES ? '(hidden)' : msg, success: /Imported/.test(msg) });
    }
  }

  // uploading the first file again must offer nothing new
  if (FILES.length) {
    await upload(page, FILES[0]);
    const hasConfirm = (await page.locator('button:has-text("Confirm import")').count()) > 0;
    const body = (await page.locator('main').innerText()).replace(/\s+/g, ' ');
    await page.screenshot({ path: path.join(SHOTS, `${PREFIX}_reupload_1440.png`), fullPage: true });
    rec('re-upload first file', { hasConfirm, saysNothingNew: /nothing new to import/i.test(body), alreadyImportedNote: /already in the system/i.test(body) });
  }

  // a bad file gives a readable error, not a traceback
  const bad = path.join(path.dirname(OUT), 'not_a_statement.txt');
  fs.writeFileSync(bad, 'hello');
  await upload(page, bad);
  rec('bad file', { url: page.url().replace(BASE, ''), message: await flash(page) });
  fs.unlinkSync(bad);

  // phone width: the preview must not overflow horizontally and stays usable
  const phone = await browser.newContext({ viewport: { width: 375, height: 800 } });
  const mp = await phone.newPage();
  const phoneErrors = [];
  mp.on('pageerror', (e) => phoneErrors.push(String(e).slice(0, 200)));
  await login(mp);
  if (FILES.length) {
    await mp.goto(BASE + '/settings/import/', { waitUntil: 'networkidle' });
    const formOv = await overflow(mp);
    await mp.screenshot({ path: path.join(SHOTS, `${PREFIX}_form_375.png`), fullPage: true });
    // a file with data that is NOT yet imported would be best, but a re-upload also renders the page: use the last file
    await upload(mp, FILES[FILES.length - 1]);
    const ov = await overflow(mp);
    await mp.screenshot({ path: path.join(SHOTS, `${PREFIX}_preview_375.png`), fullPage: true });
    rec('phone 375', { formOverflow: formOv.scrollWidth > formOv.innerWidth, previewOverflow: ov.scrollWidth > ov.innerWidth, pageErrors: phoneErrors });
  }

  R.consoleErrors = consoleErrors;
  fs.writeFileSync(OUT, JSON.stringify(R, null, 2));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
