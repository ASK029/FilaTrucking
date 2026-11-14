// Horizontal overflow check: node overflow_check.cjs <path> ...   (375 and 768 px)
const { chromium } = require('playwright');
const BASE = process.env.BASE || 'http://127.0.0.1:18081';
(async () => {
  const browser = await chromium.launch();
  for (const width of [375, 768]) {
    const ctx = await browser.newContext({ viewport: { width, height: 900 } });
    const page = await ctx.newPage();
    await page.goto(BASE + '/auth/login/');
    await page.fill('input[name=username]', 'qa_admin'); await page.fill('input[name=password]', 'qa-pass-123');
    await page.click('button[type=submit]'); await page.waitForLoadState('networkidle');
    for (const p of process.argv.slice(2)) {
      await page.goto(BASE + p, { waitUntil: 'networkidle' });
      const sw = await page.evaluate(() => document.documentElement.scrollWidth);
      console.log(`${width}px ${p}: scrollWidth=${sw} ${sw > width ? 'OVERFLOW' : 'ok'}`);
    }
    await ctx.close();
  }
  await browser.close();
})();
