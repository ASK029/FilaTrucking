// QA UI crawl: every page x 3 viewports. Output: qa/screenshots/*.png and qa/evidence/ui_crawl.json
// Usage: BASE=http://127.0.0.1:18080 node qa/tools/ui_crawl.cjs
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const BASE = process.env.BASE || 'http://127.0.0.1:18080';
const ROOT = path.resolve(__dirname, '..');
const SHOTS = path.join(ROOT, 'screenshots');
const OUT = path.join(ROOT, 'evidence', 'ui_crawl.json');
fs.mkdirSync(SHOTS, { recursive: true });
const axeSrc = fs.readFileSync(require.resolve('axe-core/axe.min.js'), 'utf8');

const PAGES = [
  '/', '/customers/', '/customers/create/', '/customers/1/', '/customers/1/edit/',
  '/drivers/', '/drivers/create/', '/drivers/1/', '/drivers/1/edit/', '/drivers/1/delete/',
  '/shipments/', '/shipments/create/', '/shipments/1/', '/shipments/2/', '/shipments/2/edit/', '/shipments/2/delete/',
  '/invoices/', '/invoices/create/', '/invoices/1/', '/invoices/1/lines/', '/invoices/1/line-item/add/',
  '/expenses/', '/expenses/create/', '/expenses/1/edit/', '/expenses/1/delete/',
  '/vehicles/', '/vehicles/create/', '/vehicles/1/', '/vehicles/1/edit/', '/vehicles/gomotive/',
  '/vehicles/ifta/logs/', '/vehicles/ifta/log/miles/', '/vehicles/ifta/log/gallons/', '/vehicles/ifta/report/?year=2026&month=11',
  '/vehicles/maintenance/', '/vehicles/maintenance/create/', '/vehicles/maintenance/1/edit/',
  '/settings/system/', '/settings/import/', '/shipments/settings/whatsapp/',
  '/reports/financial/monthly/', '/reports/financial/yearly/', '/does-not-exist/',
];
const VIEWPORTS = [{ w: 375, h: 812 }, { w: 768, h: 1024 }, { w: 1440, h: 900 }];
const slug = (p) => (p.replace(/[^a-z0-9]+/gi, '_').replace(/^_|_$/g, '') || 'home');

(async () => {
  const browser = await chromium.launch();
  const results = [];
  const hrefs = new Set();

  // --- login page (anonymous) ---
  {
    const ctx = await browser.newContext({ viewport: { width: 375, height: 812 } });
    const page = await ctx.newPage();
    await page.goto(BASE + '/auth/login/', { waitUntil: 'networkidle' });
    await page.screenshot({ path: path.join(SHOTS, 'login_375.png'), fullPage: true });
    await ctx.close();
  }

  for (const vp of VIEWPORTS) {
    const ctx = await browser.newContext({ viewport: { width: vp.w, height: vp.h } });
    const page = await ctx.newPage();
    await page.goto(BASE + '/auth/login/');
    await page.fill('input[name=username]', 'qa_admin');
    await page.fill('input[name=password]', 'qa-pass-123');
    await Promise.all([page.waitForNavigation(), page.click('button[type=submit], input[type=submit]')]);

    for (const p of PAGES) {
      const rec = { path: p, viewport: vp.w, console: [], pageErrors: [], failedRequests: [], badResponses: [] };
      const onConsole = (m) => { if (['error', 'warning'].includes(m.type())) rec.console.push(`${m.type()}: ${m.text().slice(0, 200)}`); };
      const onErr = (e) => rec.pageErrors.push(String(e).slice(0, 300));
      const onFail = (r) => rec.failedRequests.push(`${r.method()} ${r.url().slice(0, 120)} :: ${r.failure()?.errorText}`);
      const onResp = (r) => { if (r.status() >= 400) rec.badResponses.push(`${r.status()} ${r.url().slice(0, 120)}`); };
      page.on('console', onConsole); page.on('pageerror', onErr); page.on('requestfailed', onFail); page.on('response', onResp);
      try {
        const resp = await page.goto(BASE + p, { waitUntil: 'networkidle', timeout: 45000 });
        rec.status = resp.status();
        await page.waitForTimeout(400);
        const m = await page.evaluate((vw) => {
          const vis = (el) => { const r = el.getBoundingClientRect(); const s = getComputedStyle(el); return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none'; };
          const name = (el) => (el.getAttribute('aria-label') || el.textContent || el.getAttribute('title') || el.getAttribute('alt') || '').trim();
          const out = {};
          out.title = document.title; out.lang = document.documentElement.lang; out.h1 = document.querySelectorAll('h1').length;
          out.hOverflow = document.documentElement.scrollWidth > vw + 1;
          out.scrollWidth = document.documentElement.scrollWidth;
          const offenders = [];
          document.querySelectorAll('body *').forEach((el) => { if (!vis(el)) return; const r = el.getBoundingClientRect(); if (r.right > vw + 1 && !el.closest('.overflow-x-auto, [class*=overflow-x]')) offenders.push((el.tagName + '.' + (el.className || '').toString().split(' ').slice(0, 3).join('.')).slice(0, 80)); });
          out.overflowOffenders = [...new Set(offenders)].slice(0, 5);
          out.smallTargets = [...document.querySelectorAll('a[href],button,input:not([type=hidden]),select,textarea,[role=button]')].filter(vis).filter((el) => { const r = el.getBoundingClientRect(); return r.height < 44 || r.width < 44; }).length;
          out.totalTargets = [...document.querySelectorAll('a[href],button,input:not([type=hidden]),select,textarea')].filter(vis).length;
          out.imgsNoAlt = [...document.images].filter((i) => !i.hasAttribute('alt')).length;
          out.imgsBroken = [...document.images].filter((i) => i.complete && i.naturalWidth === 0).length;
          out.inputsNoLabel = [...document.querySelectorAll('input:not([type=hidden]):not([type=submit]),select,textarea')].filter(vis).filter((i) => !(i.getAttribute('aria-label') || i.getAttribute('aria-labelledby') || (i.id && document.querySelector(`label[for="${i.id}"]`)) || i.closest('label'))).length;
          out.unnamedControls = [...document.querySelectorAll('button,a[href]')].filter(vis).filter((el) => !name(el) && !el.querySelector('img[alt]')).length;
          out.hasTailwindCdn = !!document.querySelector('script[src*="cdn.tailwindcss.com"]');
          out.hasMain = !!document.querySelector('main');
          out.bodyTextLen = document.body.innerText.length;
          out.errorText = /Server Error|NoReverseMatch|Traceback|Exception Value/i.test(document.body.innerText);
          out.links = [...document.querySelectorAll('a[href]')].map((a) => a.getAttribute('href')).filter((h) => h && h.startsWith('/'));
          return out;
        }, vp.w);
        m.links.forEach((h) => hrefs.add(h.split('#')[0]));
        delete m.links;
        Object.assign(rec, m);
        if (vp.w === 1440 && p !== '/does-not-exist/') {
          await page.evaluate(axeSrc);
          const ax = await page.evaluate(async () => { const r = await axe.run(document, { resultTypes: ['violations'] }); return r.violations.map((v) => ({ id: v.id, impact: v.impact, nodes: v.nodes.length })); });
          rec.axe = ax;
        }
        await page.screenshot({ path: path.join(SHOTS, `${slug(p)}_${vp.w}.png`), fullPage: true });
      } catch (e) { rec.error = String(e).slice(0, 300); }
      page.off('console', onConsole); page.off('pageerror', onErr); page.off('requestfailed', onFail); page.off('response', onResp);
      results.push(rec);
      console.log(`${vp.w} ${p} -> ${rec.status} overflow=${rec.hOverflow} err=${rec.pageErrors.length}/${rec.console.length}`);
    }

    if (vp.w === 1440) { // link check once, as the logged-in admin
      const bad = [];
      const skip = /logout|\/delete\/|\/pdf\/|\/email\/|\/sync\/|trigger|\/api\//;
      for (const h of [...hrefs].filter((h) => !skip.test(h))) {
        const r = await page.evaluate(async (u) => { try { const x = await fetch(u, { credentials: 'same-origin' }); return x.status; } catch (e) { return -1; } }, h);
        if (r >= 400 || r === -1) bad.push(`${r} ${h}`);
      }
      results.push({ linkCheck: { checked: hrefs.size, broken: bad } });
    }
    await ctx.close();
  }
  await browser.close();
  fs.writeFileSync(OUT, JSON.stringify(results, null, 1));
  console.log('DONE ->', OUT);
})().catch((e) => { console.error(e); process.exit(1); });
