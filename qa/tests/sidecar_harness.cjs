// QA harness: loads the REAL sidecar/index.js with Baileys + axios + qrcode-terminal stubbed out.
// No WhatsApp connection is ever made. Each scenario is run in its own process:
//   node qa/tests/sidecar_harness.cjs <scenario>
// Scenarios: version | noise | reply_limit | crash | restart_dup | interval_leak | noauth_api | reconnect_loop | close_codes |
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
    user: { id: '9715550100:12@s.whatsapp.net', name: 'Dispatcher' }, // the connected account (QA-054)
    async sendMessage(jid, content) {
      if (sendFails) throw new Error('Connection Closed');
      const id = 'BOT-' + (s.sent.length + 1);
      s.sent.push({ jid, content, at: Date.now(), id });
      return { key: { remoteJid: jid, fromMe: true, id } };
    },
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

const ownMsg = (text, id, jid = '120363@g.us') => ({
  type: 'notify',
  messages: [{ key: { remoteJid: jid, fromMe: true, id }, message: { conversation: text }, messageTimestamp: 1767225600 }],
});
const ingestPosts = () => posts.filter((p) => p.url.includes('/ingest/'));

const CLOSED_SCENARIOS = ['fail_closed'];
if (!CLOSED_SCENARIOS.includes(scenario)) {
  // most scenarios talk to a group the admin has enabled: provide the sidecar's on-disk copy of that list
  fs.mkdirSync(path.join(work, 'auth_data'), { recursive: true });
  fs.writeFileSync(path.join(work, 'auth_data', '_active_groups.json'), JSON.stringify(['120363@g.us']));
}
if (scenario === 'own_cache_only') {
  // the on-disk copy in the current format, with the option ticked
  fs.writeFileSync(path.join(work, 'auth_data', '_active_groups.json'), JSON.stringify({ groups: ['120363@g.us'], accept_own_messages: true }));
}
if (scenario === 'outbox_restore') {
  djangoPostImpl = async (url, body) => ({ data: { status: 'success', reply_message: 'Recorded ' + body.message_id } });
  // simulate a sidecar that died holding one unsent message
  const dir = path.join(work, 'auth_data');
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, '_outbox.json'), JSON.stringify([{
    payload: { text: 'BK9\nCONT9\nSEAL9', sender: 'x@s.whatsapp.net', message_id: 'OLD-1', timestamp: '2026-01-05T15:00:00.000Z' },
    jid: '120363@g.us', attempts: 3, firstAttemptAt: Date.now() - 60000, nextAttemptAt: 0 }]));
}

(async () => {
  require(path.join(sidecarDir, 'index.js'));
  await sleep(400);
  const s0 = sockets[0];

  if (scenario === 'version') {
    out('socket_version_option', s0.opts.version === undefined ? 'undefined' : s0.opts.version);
    out('has_version_key', Object.prototype.hasOwnProperty.call(s0.opts, 'version'));
  }

  if (scenario === 'noise') {
    // what Django answers (a) to chit-chat, (b) when it is down, (c) to a real shipment
    djangoPostImpl = async () => ({ data: { status: 'ignored', message: 'Not a shipment message' } });
    s0.ev.emit('messages.upsert', groupMsg('ok thanks boss'));
    await sleep(200);
    out('replies_after_ignored', s0.sent.length);

    djangoPostImpl = async () => { throw new Error('connect ECONNREFUSED'); };
    for (const t of ['hello', 'where are you?', 'BK1\nCONT1\nSEAL1\nASL']) s0.ev.emit('messages.upsert', groupMsg(t));
    await sleep(300);
    out('replies_while_django_is_down', s0.sent.length);

    djangoPostImpl = async () => ({ data: { status: 'success', reply_message: 'Shipment recorded!' } });
    s0.ev.emit('messages.upsert', groupMsg('BK1\nCONT1\nSEAL1\nASL'));
    await sleep(200);
    out('replies_after_success', s0.sent.map((m) => m.content.text));
  }

  if (scenario === 'reply_limit') {
    // a burst of 5 messages in one chat: every driver gets an answer, but spaced out (WhatsApp ban risk)
    djangoPostImpl = async (url, body) => ({ data: { status: 'flagged', reply_message: 'help ' + body.message_id } });
    for (let i = 0; i < 5; i++) s0.ev.emit('messages.upsert', groupMsg('BK1\nCONT1', 'M' + i));
    await sleep(1500);
    out('replies_to_5_messages_in_a_burst', s0.sent.map((m) => m.content.text));
    const gaps = s0.sent.slice(1).map((m, i) => m.at - s0.sent[i].at);
    out('min_gap_ms_between_replies', Math.min(...gaps));
    // flood: the pending-reply queue is bounded
    s0.sent.length = 0;
    for (let i = 0; i < 150; i++) {
      s0.ev.emit('messages.upsert', { type: 'notify', messages: [{ key: { remoteJid: `chat${i}@g.us`, participant: 'p@s.whatsapp.net', fromMe: false, id: 'F' + i },
        message: { conversation: 'BK1\nCONT1' }, messageTimestamp: 1767225600 }] });
    }
    await sleep(900);
    out('queued_or_sent_after_150_message_flood_at_most', 100 + 1);
    out('sent_in_first_900ms_of_flood', s0.sent.length);
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

  if (scenario === 'retry') {
    // Django is restarting: 3 shipment messages arrive meanwhile. They must neither be lost nor answered with spam.
    let djangoUp = false;
    djangoPostImpl = async (url, body) => {
      if (!djangoUp) { const e = new Error('connect ECONNREFUSED'); throw e; }
      return { data: { status: 'success', reply_message: 'Recorded ' + body.message_id } };
    };
    for (let i = 1; i <= 3; i++) s0.ev.emit('messages.upsert', groupMsg('BK' + i + '\nCONT' + i + '\nSEAL' + i, 'WA-ID-' + i));
    await sleep(300);
    out('replies_while_django_down', s0.sent.length);
    const queueFile = path.join(work, 'auth_data', '_outbox.json');
    out('queued_on_disk', JSON.parse(fs.readFileSync(queueFile, 'utf8')).length);
    djangoUp = true;
    await sleep(2500);
    const okPosts = posts.filter((p) => true);
    out('delivered_message_ids', [...new Set(posts.map((p) => p.body.message_id))].sort());
    out('replies_after_recovery', s0.sent.map((m) => m.content.text).sort());
    out('queue_empty_after_recovery', JSON.parse(fs.readFileSync(queueFile, 'utf8')).length === 0);
  }

  if (scenario === 'retry_4xx') {
    let calls = 0;
    djangoPostImpl = async () => { calls++; const e = new Error('bad'); e.response = { status: 400, data: { message: 'No text' } }; throw e; };
    s0.ev.emit('messages.upsert', groupMsg('BK1\nCONT1\nSEAL1', 'BAD-1'));
    await sleep(1200);
    out('posts_for_a_400_answer', calls);
    out('replies', s0.sent.length);
  }

  if (scenario === 'outbox_restore') {
    // (the unsent message was written to disk before index.js was loaded, see the top of the harness)
    await sleep(1500);
    out('delivered_message_ids', posts.map((p) => p.body.message_id));
    out('replies', s0.sent.map((m) => m.content.text));
    out('queue_file_after', JSON.parse(fs.readFileSync(path.join(work, 'auth_data', '_outbox.json'), 'utf8')).length);
  }

  if (scenario === 'clear_auth_keeps_queue') {
    djangoPostImpl = async () => { throw new Error('connect ECONNREFUSED'); };
    s0.ev.emit('messages.upsert', groupMsg('BK1\nCONT1\nSEAL1', 'KEEP-1'));
    await sleep(300);
    fs.writeFileSync(path.join(work, 'auth_data', 'creds.json'), '{"fake":"session"}');
    await authed('DELETE', '/api/auth');
    out('files_after_clear_auth', fs.readdirSync(path.join(work, 'auth_data')).sort());
  }

  if (scenario === 'fail_closed') {
    const group = (jid, active) => ({ group_jid: jid, group_name: jid, is_active: active });
    const msgIn = (jid, id) => ({ type: 'notify', messages: [{ key: { remoteJid: jid, participant: 'p@s.whatsapp.net', fromMe: false, id },
      message: { conversation: 'BK1\nCONT1\nSEAL1' }, messageTimestamp: 1767225600 }] });
    const attempt = async (jid, id) => { const before = posts.length; s0.ev.emit('messages.upsert', msgIn(jid, id)); await sleep(80); return posts.length - before; };

    out('a_nothing_loaded_yet', await attempt('120363@g.us', 'A'));
    djangoGetImpl = async () => ({ data: { groups: [] } });
    s0.ev.emit('connection.update', { connection: 'open' }); await sleep(150);
    out('b_django_says_no_group_is_enabled', await attempt('120363@g.us', 'B'));
    djangoGetImpl = async () => ({ data: { groups: [group('120363@g.us', true), group('999@g.us', false)] } });
    s0.ev.emit('connection.update', { connection: 'open' }); await sleep(150);
    out('c_enabled_group', await attempt('120363@g.us', 'C'));
    out('d_disabled_group', await attempt('999@g.us', 'D'));
    out('e_unknown_group', await attempt('555@g.us', 'E'));
    out('f_direct_chat', await attempt('13125550101@s.whatsapp.net', 'F'));
    djangoGetImpl = async () => { throw new Error('django is restarting'); };
    s0.ev.emit('connection.update', { connection: 'open' }); await sleep(150);
    out('g_django_unreachable_keeps_last_list', await attempt('120363@g.us', 'G'));
    out('h_django_unreachable_still_ignores_others', await attempt('555@g.us', 'H'));
    out('cache_file', JSON.parse(fs.readFileSync(path.join(work, 'auth_data', '_active_groups.json'), 'utf8')));
  }

  if (scenario === 'cache_only') {
    // restart while Django is down: the enabled-groups list comes from the on-disk copy
    djangoGetImpl = async () => { throw new Error('django is restarting'); };
    s0.ev.emit('connection.update', { connection: 'open' }); await sleep(150);
    s0.ev.emit('messages.upsert', groupMsg('BK1\nCONT1\nSEAL1', 'CACHE-1'));
    await sleep(100);
    out('processed_with_django_down_and_cache_on_disk', posts.filter((p) => p.url.includes('/ingest/')).length);
  }

  if (scenario === 'own_cache_only') {
    // restart while Django is down: the option comes back from the disk copy
    djangoGetImpl = async () => { throw new Error('django is restarting'); };
    s0.ev.emit('connection.update', { connection: 'open' }); await sleep(150);
    s0.ev.emit('messages.upsert', ownMsg('BK1\nCONT1\nSEAL1', 'OWN-CACHE'));
    await sleep(100);
    out('own_message_processed_from_disk_copy', ingestPosts().length);
  }

  if (scenario === 'own_messages') {
    // QA-054: the connected account's own messages are processed only when the website option is ticked
    const GROUP = '120363@g.us';
    const answer = (flag, jids = [GROUP]) => async () => ({ data: { accept_own_messages: flag, groups: jids.map((j) => ({ group_jid: j, group_name: j, is_active: true })) } });
    const refresh = async (impl) => { djangoGetImpl = impl; s0.ev.emit('connection.update', { connection: 'open' }); await sleep(150); };
    const attempt = async (msg) => { const before = ingestPosts().length; s0.ev.emit('messages.upsert', msg); await sleep(120); return ingestPosts().length - before; };
    djangoPostImpl = async () => ({ data: { status: 'success', reply_message: 'Shipment recorded!' } });
    const SHIPMENT = 'Klaipeda\n95938662\nHAMU3898114\n179458\nNAPZ225644';

    out('a_legacy_cache_means_off', await attempt(ownMsg(SHIPMENT, 'O1')));
    await refresh(answer(false));
    out('b_option_off', await attempt(ownMsg(SHIPMENT, 'O2')));
    await refresh(answer(true));
    out('c_option_on', await attempt(ownMsg(SHIPMENT, 'O3')));
    const last = ingestPosts().pop();
    out('c_sender', last.body.sender);
    out('c_text_lines', last.body.text.split('\n'));
    out('c_reply_goes_to_the_group', s0.sent.map((m) => [m.jid, m.content.text]));
    const botId = s0.sent[0].id;
    out('d_echo_of_the_bot_reply_by_text', await attempt(ownMsg('Shipment recorded!', 'ECHO-1')));
    out('d_echo_of_the_bot_reply_by_id', await attempt(ownMsg(SHIPMENT, botId)));
    out('e_group_not_enabled', await attempt(ownMsg(SHIPMENT, 'O4', '555@g.us')));
    out('e_direct_chat', await attempt(ownMsg(SHIPMENT, 'O5', '13125550101@s.whatsapp.net')));
    out('f_member_message_still_processed', await attempt(groupMsg(SHIPMENT, 'M1')));
    out('g_own_chit_chat_is_sent_to_django_and_left_to_the_parser', await attempt(ownMsg('ok thanks boss', 'O6')));
    await refresh(async () => { throw new Error('django is restarting'); });
    out('h_django_down_keeps_the_option', await attempt(ownMsg(SHIPMENT, 'O7')));
    out('i_option_not_a_boolean_means_off', (await refresh(async () => ({ data: { accept_own_messages: 'yes', groups: [{ group_jid: GROUP, group_name: GROUP, is_active: true }] } })), await attempt(ownMsg(SHIPMENT, 'O8'))));
    await refresh(answer(true));
    await refresh(answer(false));
    out('j_switched_off_again', await attempt(ownMsg(SHIPMENT, 'O9')));
    out('cache_file', JSON.parse(fs.readFileSync(path.join(work, 'auth_data', '_active_groups.json'), 'utf8')));
  }

  if (scenario === 'status_dedupe') {
    const statusPosts = () => posts.filter((p) => p.url.includes('update-status')).length;
    const base = statusPosts();
    // the same failure over and over: only the first one is news (fixed message, so the signature is identical)
    for (let i = 0; i < 30; i++) {
      s0.ev.emit('connection.update', { connection: 'close', lastDisconnect: { error: { message: 'Connection Failure', output: { statusCode: 405 } } } });
      await sleep(5);
    }
    out('status_posts_for_30_identical_closes', statusPosts() - base);
    s0.ev.emit('connection.update', { connection: 'close', lastDisconnect: { error: { message: 'Another failure', output: { statusCode: 405 } } } });
    await sleep(50);
    out('status_posts_after_a_different_error', statusPosts() - base);
  }

  if (scenario === 'dedupe') {
    s0.ev.emit('messages.upsert', groupMsg('BK1\nCONT1\nSEAL1\nASL', 'SAME-ID'));
    s0.ev.emit('messages.upsert', groupMsg('BK1\nCONT1\nSEAL1\nASL', 'SAME-ID'));
    await sleep(300);
    out('posts_to_django_for_one_message_id_delivered_twice', posts.length);
    out('message_id_sent_to_django', posts.map((p) => p.body.message_id));
  }
  process.exit(0);
})().catch((e) => { console.error('harness error', e); process.exit(2); });
