// QA harness: loads the REAL sidecar/index.js with Baileys + axios + qrcode-terminal stubbed out.
// No WhatsApp connection is ever made. Each scenario is run in its own process:
//   node qa/tests/sidecar_harness.cjs <scenario>
// Scenarios: version | noise | crash | restart_dup | interval_leak | noauth_api | reconnect_loop | close_codes |
//            qr_timeouts | no_stacking | dedupe
const Module = require('module');
const path = require('path');
const fs = require('fs');
const os = require('os');
const http = require('http');
const { EventEmitter } = require('events');

const scenario = process.argv[2];
const sidecarDir = path.resolve(__dirname, '..', '..', 'sidecar');
const work = fs.mkdtempSync(path.join(os.tmpdir(), 'qa-sidecar-'));
process.chdir(work); // index.js stores auth in process.cwd()/auth_data
const PORT = 34000 + Math.floor(Math.random() * 1000);
process.env.PORT = String(PORT);
process.env.DJANGO_API_URL = 'http://django.invalid/shipments/api/ingest/';

const out = (k, v) => console.log(`RESULT ${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// ---- stubs -----------------------------------------------------------------
const sockets = [];
let sendFails = false;
function makeWASocket(opts) {
  const s = {
    opts, ev: new EventEmitter(), sent: [], ended: false, createdAt: Date.now(),
    ws: { close() {} },
    async sendMessage(jid, content) { if (sendFails) throw new Error('Connection Closed'); s.sent.push({ jid, content }); },
    end() { s.ended = true; setImmediate(() => s.ev.emit('connection.update', { connection: 'close', lastDisconnect: { error: { output: { statusCode: 428 } } } })); },
    async groupFetchAllParticipating() { return {}; },
  };
  sockets.push(s);
  return s;
}
const posts = [];
let djangoPostImpl = async () => ({ data: { status: 'success' } });
let djangoGetImpl = async () => ({ data: { groups: [] } });
const fakeAxios = {
  post: async (url, body) => { posts.push({ url, body }); return djangoPostImpl(url, body); },
  get: async (url) => djangoGetImpl(url),
};
const fakeBaileys = {
  default: makeWASocket,
  fetchLatestBaileysVersion: async () => {
    if (process.env.HARNESS_FETCH === 'fail') throw new Error('github unreachable');
    return { version: [2, 3000, 999], isLatest: true };
  },
  useMultiFileAuthState: async () => ({ state: {}, saveCreds: async () => {} }),
  DisconnectReason: { loggedOut: 401, connectionReplaced: 440, restartRequired: 515, timedOut: 408, connectionClosed: 428 },
};
const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === '@whiskeysockets/baileys') return fakeBaileys;
  if (request === 'axios') return fakeAxios;
  if (request === 'qrcode-terminal') return { generate() {} };
  return origLoad.apply(this, arguments);
};
// track long-lived intervals (index.js registers a 30 s poll)
const intervals = [];
const origSetInterval = global.setInterval;
global.setInterval = function (fn, ms, ...a) { const h = origSetInterval(fn, ms, ...a); intervals.push({ ms, h }); return h; };
// "fake clock" for reconnect delays: record every timer >= 400 ms and never really wait for it
const timers = [];
const origSetTimeout = global.setTimeout;
let fakeClock = false;
global.setTimeout = function (fn, ms, ...a) {
  if (fakeClock && ms >= 400) {
    const rec = { ms, fn, cleared: false };
    timers.push(rec);
    const h = origSetTimeout(() => {}, 1);
    rec.handle = h;
    return h;
  }
  return origSetTimeout(fn, ms, ...a);
};
const origClearTimeout = global.clearTimeout;
global.clearTimeout = function (h) {
  for (const t of timers) if (t.handle === h) t.cleared = true;
  return origClearTimeout(h);
};
const pendingTimers = () => timers.filter((t) => !t.cleared && !t.fired);
const fire = (t) => { t.fired = true; t.fn(); };

const SECRET = process.env.DJANGO_SECRET;
function req(method, p, body, headers = {}) {
  return new Promise((resolve, reject) => {
    const r = http.request({ host: '127.0.0.1', port: PORT, path: p, method, headers: { 'Content-Type': 'application/json', ...headers } }, (res) => {
      let d = ''; res.on('data', (c) => (d += c)); res.on('end', () => resolve({ status: res.statusCode, body: d }));
    });
    r.on('error', reject); if (body) r.write(JSON.stringify(body)); r.end();
  });
}
const authed = (method, p, body) => req(method, p, body, { Authorization: `Bearer ${SECRET}` });
const closeEvent = (code) => ({ connection: 'close', lastDisconnect: { error: { message: 'x', output: { statusCode: code } } } });
const groupMsg = (text, id = 'MSG1') => ({
  type: 'notify',
  messages: [{ key: { remoteJid: '120363@g.us', participant: '13125550101@s.whatsapp.net', fromMe: false, id },
               message: { conversation: text }, messageTimestamp: 1767225600 }],
});

(async () => {
  require(path.join(sidecarDir, 'index.js'));
  await sleep(400);
  const s0 = sockets[0];

  if (scenario === 'version') {
    out('socket_version_option', s0.opts.version === undefined ? 'undefined' : s0.opts.version);
    out('has_version_key', Object.prototype.hasOwnProperty.call(s0.opts, 'version'));
  }

  if (scenario === 'noise') {
    // Django's real answer to chit-chat (see qa/tests/test_whatsapp_ingest.py::test_chat_noise_*):
    djangoPostImpl = async () => ({ data: { status: 'flagged', message: 'Missing fields, flagged for manual review.', message_id: 1 } });
    s0.ev.emit('messages.upsert', groupMsg('ok thanks boss'));
    await sleep(300);
    out('replies_sent_to_group', s0.sent.map((m) => m.content.text));
  }

  if (scenario === 'crash') {
    // Django unreachable AND socket already closed -> the catch{} block's sendMessage rejects, nobody handles it.
    djangoPostImpl = async () => { throw new Error('connect ECONNREFUSED'); };
    sendFails = true;
    process.on('exit', (c) => console.log('RESULT process_exit_code=' + c));
    s0.ev.emit('messages.upsert', groupMsg('BK1\nCONT1\nSEAL1\nASL'));
    await sleep(800);
    out('still_alive_after_800ms', true); // only printed if no crash
  }

  if (scenario === 'restart_dup') {
    s0.ev.emit('connection.update', { connection: 'open' });
    await sleep(200);
    const before = sockets.length;
    const r = await authed('POST', '/api/restart');
    await sleep(2500);
    out('http_status', r.status);
    out('sockets_created_by_one_restart_call', sockets.length - before);
    out('sockets_still_open_with_same_credentials', sockets.filter((s) => !s.ended).length);
    // and a double-click on "Restart" must still produce a single new socket
    const before2 = sockets.length;
    await authed('POST', '/api/restart');
    await authed('POST', '/api/restart');
    await sleep(2500);
    out('sockets_created_by_two_quick_restart_calls', sockets.length - before2);
    out('sockets_open_after_two_quick_restarts', sockets.filter((s) => !s.ended).length);
  }

  if (scenario === 'interval_leak') {
    for (let i = 0; i < 4; i++) s0.ev.emit('connection.update', { connection: 'open' });
    await sleep(300);
    out('open_events', 4);
    out('30s_intervals_registered', intervals.filter((i) => i.ms === 30000).length);
  }

  if (scenario === 'noauth_api') {
    fs.mkdirSync(path.join(work, 'auth_data'), { recursive: true });
    fs.writeFileSync(path.join(work, 'auth_data', 'creds.json'), '{"fake":"session"}');
    s0.ev.emit('connection.update', { qr: '2@FAKE-QR-PAYLOAD,abc,def' });
    await sleep(100);
    const qr = await req('GET', '/api/qr-code');
    out('GET /api/qr-code (no auth)', { status: qr.status, leaks_qr: qr.body.includes('FAKE-QR-PAYLOAD') });
    out('GET /api/logs (no auth)', (await req('GET', '/api/logs')).status);
    out('GET /api/groups (no auth)', (await req('GET', '/api/groups')).status);
    out('GET /api/status (no auth)', (await req('GET', '/api/status')).status);
    out('POST /api/restart (no auth)', (await req('POST', '/api/restart')).status);
    out('DELETE /api/auth (no auth)', (await req('DELETE', '/api/auth')).status);
    out('DELETE /api/auth (wrong secret)', (await req('DELETE', '/api/auth', null, { Authorization: 'Bearer wrong-secret-value-123456' })).status);
    out('DELETE /api/auth (Basic scheme)', (await req('DELETE', '/api/auth', null, { Authorization: `Basic ${SECRET}` })).status);
    out('session_files_left_after_unauthenticated_calls', fs.readdirSync(path.join(work, 'auth_data')));
    out('GET /health (no auth)', await req('GET', '/health').then((r) => ({ status: r.status, body: r.body })));
    const ok = await authed('GET', '/api/qr-code');
    out('GET /api/qr-code (with secret)', { status: ok.status, has_qr: ok.body.includes('FAKE-QR-PAYLOAD') });
  }

  if (scenario === 'reconnect_loop') {
    // WhatsApp keeps answering with a status code: how long does the sidecar wait between attempts?
    fakeClock = true;
    const code = parseInt(process.env.HARNESS_CODE || '405', 10);
    const delays = [];
    let current = s0;
    for (let i = 0; i < 12; i++) {
      current.ev.emit('connection.update', closeEvent(code));
      await sleep(30);
      const pending = pendingTimers();
      if (pending.length !== 1) { out('pending_timers_after_close', pending.length); break; }
      delays.push(Math.round(pending[0].ms / 1000));
      fire(pending[0]);
      await sleep(60);
      current = sockets[sockets.length - 1];
    }
    out('delays_seconds', delays);
    out('sockets_created', sockets.length);
  }

  if (scenario === 'close_codes') {
    fakeClock = true;
    fs.mkdirSync(path.join(work, 'auth_data'), { recursive: true });
    const res = {};
    for (const code of [401, 440, 515, 405, 428]) {
      fs.writeFileSync(path.join(work, 'auth_data', 'creds.json'), '{"fake":"session"}');
      const cur = sockets[sockets.length - 1];
      cur.ev.emit('connection.update', closeEvent(code));
      await sleep(50);
      const pending = pendingTimers();
      res[code] = { delay_ms: pending.length ? pending[0].ms : null, auth_files_left: fs.readdirSync(path.join(work, 'auth_data')).length };
      pending.forEach(fire);
      await sleep(80);
    }
    out('per_status_code', res);
  }

  if (scenario === 'qr_timeouts') {
    // pairing: QR shown, nobody scans, WhatsApp closes with 408 over and over -> short capped delays
    fakeClock = true;
    const delays = [];
    let current = s0;
    for (let i = 0; i < 8; i++) {
      current.ev.emit('connection.update', { qr: '2@QR' + i });
      current.ev.emit('connection.update', closeEvent(408));
      await sleep(30);
      const p = pendingTimers();
      delays.push(Math.round(p[0].ms / 1000));
      fire(p[0]);
      await sleep(60);
      current = sockets[sockets.length - 1];
    }
    out('delays_seconds', delays);
  }

  if (scenario === 'no_stacking') {
    fakeClock = true;
    s0.ev.emit('connection.update', closeEvent(428));
    await sleep(30);
    await authed('POST', '/api/restart');
    await authed('POST', '/api/restart');
    out('pending_restart_timers_after_close_plus_two_restarts', pendingTimers().length);
  }

  if (scenario === 'dedupe') {
    s0.ev.emit('messages.upsert', groupMsg('BK1\nCONT1\nSEAL1\nASL', 'SAME-ID'));
    s0.ev.emit('messages.upsert', groupMsg('BK1\nCONT1\nSEAL1\nASL', 'SAME-ID'));
    await sleep(300);
    out('posts_to_django_for_one_message_id_delivered_twice', posts.length);
  }
  process.exit(0);
})().catch((e) => { console.error('harness error', e); process.exit(2); });
