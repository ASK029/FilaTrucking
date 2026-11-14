// QA harness: loads the REAL sidecar/index.js with Baileys + axios + qrcode-terminal stubbed out.
// No WhatsApp connection is ever made. Each scenario is run in its own process:
//   node qa/tests/sidecar_harness.cjs <scenario>
// Scenarios: noise | crash | restart_dup | interval_leak | noauth_api | reconnect_loop | dedupe
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
const fakeAxios = {
  post: async (url, body) => { posts.push({ url, body }); return djangoPostImpl(url, body); },
  get: async () => ({ data: { groups: [] } }),
};
const fakeBaileys = {
  default: makeWASocket,
  fetchLatestBaileysVersion: async () => {
    if (process.env.HARNESS_FETCH === 'fail') throw new Error('github unreachable');
    return { version: [2, 3000, 999], isLatest: true };
  },
  useMultiFileAuthState: async () => ({ state: {}, saveCreds: async () => {} }),
  DisconnectReason: { loggedOut: 401 },
};
const origLoad = Module._load;
Module._load = function (request, parent, isMain) {
  if (request === '@whiskeysockets/baileys') return fakeBaileys;
  if (request === 'axios') return fakeAxios;
  if (request === 'qrcode-terminal') return { generate() {} };
  return origLoad.apply(this, arguments);
};
// track long-lived intervals (index.js registers a 30 s poll on every 'open')
const intervals = [];
const origSetInterval = global.setInterval;
global.setInterval = function (fn, ms, ...a) { const h = origSetInterval(fn, ms, ...a); intervals.push({ ms, h }); return h; };

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
    await sleep(5500);
    out('http_status', r.status);
    out('sockets_created_by_one_restart_call', sockets.length - before);
    out('sockets_still_open_with_same_credentials', sockets.filter((s) => !s.ended).length);
  }

  if (scenario === 'interval_leak') {
    for (let i = 0; i < 4; i++) s0.ev.emit('connection.update', { connection: 'open' });
    await sleep(300);
    out('open_events', 4);
    out('30s_intervals_registered_never_cleared', intervals.filter((i) => i.ms === 30000).length);
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
    // WhatsApp keeps answering 405: how does the sidecar behave?
    const t0 = Date.now();
    const kick = (s) => setTimeout(() => s.ev.emit('connection.update', { connection: 'close', lastDisconnect: { error: { output: { statusCode: 405 }, data: { reason: '405' } } } }), 10);
    const origPush = sockets.push.bind(sockets);
    sockets.push = (s) => { kick(s); return origPush(s); };
    kick(s0);
    await sleep(13000);
    out('reconnect_attempts_in_13s', sockets.length);
    out('seconds_between_attempts', sockets.slice(1).map((s, i) => +((s.createdAt - sockets[i].createdAt) / 1000).toFixed(1)));
  }

  if (scenario === 'dedupe') {
    s0.ev.emit('messages.upsert', groupMsg('BK1\nCONT1\nSEAL1\nASL', 'SAME-ID'));
    s0.ev.emit('messages.upsert', groupMsg('BK1\nCONT1\nSEAL1\nASL', 'SAME-ID'));
    await sleep(300);
    out('posts_to_django_for_one_message_id_delivered_twice', posts.length);
  }
  process.exit(0);
})().catch((e) => { console.error('harness error', e); process.exit(2); });
