// Lists interactive elements smaller than 44x44 px at 375 px wide: node small_targets.cjs <path> [path...]
const { chromium } = require('playwright');
const BASE = process.env.BASE || 'http://127.0.0.1:18081';
(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 375, height: 812 } });
  const page = await ctx.newPage();
  await page.goto(BASE + '/auth/login/');
  await page.fill('input[name=username]', 'qa_admin');
  await page.fill('input[name=password]', 'qa-pass-123');
  await page.click('button[type=submit]');
  for (const p of process.argv.slice(2)) {
    await page.goto(BASE + p, { waitUntil: 'networkidle' });
    const rows = await page.evaluate(() => {
      const out = [];
      document.querySelectorAll('a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button]').forEach((el) => {
        const r = el.getBoundingClientRect();
        const cs = getComputedStyle(el);
        if (cs.visibility === 'hidden' || cs.display === 'none' || r.width === 0 || r.height === 0) return;
        if (r.width < 44 || r.height < 44) out.push(`${el.tagName.toLowerCase()}${el.type ? '[' + el.type + ']' : ''} ${Math.round(r.width)}x${Math.round(r.height)} "${(el.innerText || el.value || el.getAttribute('aria-label') || el.name || '').trim().slice(0, 28)}" .${(el.className || '').toString().slice(0, 50)}`);
      });
      return out;
    });
    console.log(`== ${p}: ${rows.length} small`);
    const seen = {};
    rows.forEach((r) => { const k = r.replace(/"[^"]*"/, ''); seen[k] = (seen[k] || 0) + 1; });
    Object.entries(seen).slice(0, 12).forEach(([k, n]) => console.log(`  ${n}x ${k}`));
  }
  await browser.close();
})();
