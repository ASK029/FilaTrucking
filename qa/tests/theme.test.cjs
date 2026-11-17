// QA-053: behaviour of static/js/theme.js (light / dark toggle) with a minimal fake DOM. Run: node --test qa/tests/theme.test.cjs
const test = require('node:test');
const assert = require('node:assert');
const fs = require('fs');
const path = require('path');
const vm = require('vm');

const SOURCE = fs.readFileSync(path.join(__dirname, '..', '..', 'FilaTrucking', 'static', 'js', 'theme.js'), 'utf8');

function load({ stored, storageThrows = false, buttons = 1 } = {}) {
  const attrs = {};
  const buttonEls = Array.from({ length: buttons }, () => { const a = {}; return { a, setAttribute: (k, v) => { a[k] = v; } }; });
  const listeners = {};
  const store = stored ? { 'fila-theme': stored } : {};
  const document = {
    documentElement: { setAttribute: (k, v) => { attrs[k] = v; }, getAttribute: (k) => (k in attrs ? attrs[k] : null) },
    querySelectorAll: () => buttonEls,
    addEventListener: (name, fn) => { listeners[name] = fn; },
  };
  const localStorage = storageThrows
    ? { getItem() { throw new Error('blocked'); }, setItem() { throw new Error('blocked'); } }
    : { getItem: (k) => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = v; } };
  vm.runInNewContext(SOURCE, { document, window: { localStorage } });
  const click = () => listeners.click({ target: { closest: (sel) => (sel === '[data-theme-toggle]' ? buttonEls[0] : null) } });
  return { attrs, buttonEls, store, click, listeners };
}

test('the default is the dark theme the app always had', () => {
  const { attrs, buttonEls } = load();
  assert.strictEqual(attrs['data-theme'], 'dark');
  assert.strictEqual(buttonEls[0].a['aria-label'], 'Switch to light theme');
  assert.strictEqual(buttonEls[0].a['aria-pressed'], 'false');
});

test('a saved light choice is applied immediately', () => {
  const { attrs, buttonEls } = load({ stored: 'light' });
  assert.strictEqual(attrs['data-theme'], 'light');
  assert.strictEqual(buttonEls[0].a['aria-label'], 'Switch to dark theme');
  assert.strictEqual(buttonEls[0].a['aria-pressed'], 'true');
});

test('anything else in storage is ignored', () => {
  assert.strictEqual(load({ stored: 'blue' }).attrs['data-theme'], 'dark');
});

test('the toggle flips the theme and remembers it', () => {
  const t = load();
  t.click();
  assert.strictEqual(t.attrs['data-theme'], 'light');
  assert.strictEqual(t.store['fila-theme'], 'light');
  t.click();
  assert.strictEqual(t.attrs['data-theme'], 'dark');
  assert.strictEqual(t.store['fila-theme'], 'dark');
});

test('clicks elsewhere do nothing', () => {
  const t = load();
  t.listeners.click({ target: { closest: () => null } });
  assert.strictEqual(t.attrs['data-theme'], 'dark');
});

test('blocked storage (private window) still works for the current page', () => {
  const t = load({ storageThrows: true });
  assert.strictEqual(t.attrs['data-theme'], 'dark');
  t.click();
  assert.strictEqual(t.attrs['data-theme'], 'light');
});
