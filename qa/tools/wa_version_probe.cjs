// QA diagnostic: does WhatsApp accept a *registration-only* connection (no pairing, no messages)?
// Usage: node wa_version_probe.cjs pinned|latest|default [seconds]   (SIDECAR_DIR=<dir with node_modules> to test another Baileys build)
// - default: pass NO version, i.e. whatever the installed Baileys build ships with
// - pinned: the exact version hard-coded in sidecar/index.js
// - latest: version returned by Baileys' fetchLatestBaileysVersion()
// Uses a throw-away auth dir. Never scans a QR, never sends a message, never touches production.
const { createRequire } = require('module');
const path = require('path');
const os = require('os');
const fs = require('fs');

const sidecarDir = process.env.SIDECAR_DIR || path.resolve(__dirname, '..', '..', 'sidecar');
const req = createRequire(path.join(sidecarDir, 'index.js'));
const baileys = req('@whiskeysockets/baileys');
const pino = req('pino');
const makeWASocket = baileys.default || baileys.makeWASocket;
const { useMultiFileAuthState, fetchLatestBaileysVersion } = baileys;

const mode = process.argv[2] || 'pinned';
const seconds = Number(process.argv[3] || 20);

(async () => {
  const authDir = fs.mkdtempSync(path.join(os.tmpdir(), 'qa-wa-probe-'));
  const { state } = await useMultiFileAuthState(authDir);
  let version = [2, 3000, 1034074495]; // hard-coded in sidecar/index.js:122
  if (mode === 'latest') {
    const r = await fetchLatestBaileysVersion();
    version = r.version;
    console.log('fetchLatestBaileysVersion ->', JSON.stringify(r));
  }
  console.log(`[${mode}] using WA Web version`, version.join('.'));
  const opts = {
    auth: state,
    version,
    browser: ['Fila Trucking', 'Safari', '15.0'],
    printQRInTerminal: false,
    logger: pino({ level: 'silent' }),
  };
  if (mode === 'default') { delete opts.version; console.log('[default] no version passed: library default is used'); }
  const sock = makeWASocket(opts);
  let result = 'no-event';
  sock.ev.on('connection.update', (u) => {
    if (u.qr) { result = 'QR_RECEIVED (WhatsApp accepted the client)'; console.log(result); }
    if (u.connection === 'close') {
      const code = u.lastDisconnect?.error?.output?.statusCode;
      const loc = u.lastDisconnect?.error?.data?.location;
      result = `CLOSED statusCode=${code} location=${loc}`;
      console.log(result);
    }
  });
  setTimeout(() => {
    console.log(`[${mode}] RESULT:`, result);
    try { sock.end(undefined); } catch (_) {}
    fs.rmSync(authDir, { recursive: true, force: true });
    process.exit(0);
  }, seconds * 1000);
})().catch((e) => { console.error('probe error', e); process.exit(1); });
