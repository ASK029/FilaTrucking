// Node-side regression tests for sidecar/index.js. Each scenario runs the REAL index.js in its own
// process (qa/tests/sidecar_harness.cjs stubs Baileys/axios, no WhatsApp connection is made).
//   node --test qa/tests/sidecar.test.cjs        (from the repo root)
const test = require('node:test');
const assert = require('node:assert');
const { spawnSync } = require('node:child_process');
const path = require('node:path');

const SECRET = 'qa-test-shared-secret-0123456789';
function run(scenario, env = {}) {
  const r = spawnSync(process.execPath, [path.join(__dirname, 'sidecar_harness.cjs'), scenario], {
    env: {
      ...process.env,
      NODE_PATH: [path.join(__dirname, '..', 'tools', 'node_modules'), path.join(__dirname, '..', '..', 'sidecar', 'node_modules')].join(path.delimiter),
      DJANGO_SECRET: SECRET,
      ...env,
    },
    encoding: 'utf8',
    timeout: 60000,
  });
  const res = {};
  for (const line of (r.stdout || '').split('\n')) {
    const m = line.match(/^RESULT ([^=]+)=(.*)$/);
    if (m) { try { res[m[1]] = JSON.parse(m[2]); } catch { res[m[1]] = m[2]; } }
  }
  return { res, code: r.status, stdout: r.stdout, stderr: r.stderr };
}

test('QA-001 uses the freshly fetched WhatsApp-Web version, never a hard-coded one', () => {
  const { res } = run('version');
  assert.deepStrictEqual(res.socket_version_option, [2, 3000, 999]);
});

test('QA-001 falls back to the Baileys default (no version key) when the lookup fails', () => {
  const { res } = run('version', { HARNESS_FETCH: 'fail' });
  assert.strictEqual(res.has_version_key, false);
});

test('QA-002 the sidecar REST API rejects every call without the shared secret', () => {
  const { res } = run('noauth_api');
  assert.strictEqual(res['GET /api/qr-code (no auth)'].status, 401);
  assert.strictEqual(res['GET /api/qr-code (no auth)'].leaks_qr, false);
  for (const k of ['GET /api/logs (no auth)', 'GET /api/groups (no auth)', 'GET /api/status (no auth)',
                   'POST /api/restart (no auth)', 'DELETE /api/auth (no auth)',
                   'DELETE /api/auth (wrong secret)', 'DELETE /api/auth (Basic scheme)']) {
    assert.strictEqual(res[k], 401, k);
  }
  assert.deepStrictEqual([...res.session_files_left_after_unauthenticated_calls].sort(), ['_active_groups.json', 'creds.json']);
});

test('QA-002 /health stays open but reveals nothing; the right secret is accepted', () => {
  const { res } = run('noauth_api');
  assert.strictEqual(res['GET /health (no auth)'].status, 200);
  assert.deepStrictEqual(JSON.parse(res['GET /health (no auth)'].body), { status: 'ok' });
  assert.deepStrictEqual(res['GET /api/qr-code (with secret)'], { status: 200, has_qr: true });
});

test('QA-004 the sidecar refuses to start without a real secret', () => {
  for (const bad of ['', 'short', 'fila_secret_2026']) {
    const { code, stderr } = run('version', { DJANGO_SECRET: bad });
    assert.notStrictEqual(code, 0, `secret "${bad}" must not be accepted`);
    assert.match(stderr, /DJANGO_SECRET/);
  }
});

test('QA-015 a failed reply cannot crash the process (unhandled rejection)', () => {
  const { res, code, stdout } = run('crash');
  assert.strictEqual(code, 0);
  assert.strictEqual(res.still_alive_after_800ms, true);
  assert.strictEqual(res.process_exit_code, 0);
});

test('QA-015 one restart call leaves exactly one live socket, also after a double click', () => {
  const { res } = run('restart_dup');
  assert.strictEqual(res.sockets_created_by_one_restart_call, 1);
  assert.strictEqual(res.sockets_still_open_with_same_credentials, 1);
  assert.strictEqual(res.sockets_created_by_two_quick_restart_calls, 1);
  assert.strictEqual(res.sockets_open_after_two_quick_restarts, 1);
});

test('QA-015 reconnecting never registers more than one 30 s poll timer', () => {
  const { res } = run('interval_leak');
  assert.strictEqual(res['30s_intervals_registered'], 1);
});

test('QA-015 a 405 loop backs off exponentially (1 min -> 15 min cap) instead of retrying every 3 s', () => {
  const { res } = run('reconnect_loop', { HARNESS_CODE: '405' });
  const d = res.delays_seconds;
  assert.strictEqual(d.length, 12);
  assert.ok(d[0] >= 48 && d[0] <= 72, `first delay ${d[0]}`);
  assert.ok(d[2] > d[0] * 2, 'delays must grow');
  assert.ok(Math.max(...d) <= 1080, 'capped at 15 min (+jitter)');
  assert.ok(d[11] >= 720, `settles at the cap, got ${d[11]}`);
});

test('QA-015 ordinary disconnects back off from 3 s up to a 5 min cap, with jitter', () => {
  const { res } = run('reconnect_loop', { HARNESS_CODE: '428' });
  const d = res.delays_seconds;
  assert.ok(d[0] >= 2 && d[0] <= 4, `first delay ${d[0]}`);
  assert.ok(d[11] >= 240 && d[11] <= 360, `cap ${d[11]}`);
});

test('QA-015 per-status policy: 401 clears the dead session, 440 waits >= 60 s, 515 reconnects at once', () => {
  const { res } = run('close_codes');
  const r = res.per_status_code;
  assert.strictEqual(r['401'].auth_files_left, 1, 'logged out: creds wiped (only the _active_groups.json cache remains) so a fresh QR can be issued');
  assert.ok(r['401'].delay_ms <= 3000);
  assert.ok(r['440'].delay_ms >= 60000, 'connection replaced: do not fight the other session');
  assert.ok(r['515'].delay_ms <= 1000);
  assert.strictEqual(r['515'].auth_files_left, 2, '515 must not touch the session');
  assert.strictEqual(r['405'].auth_files_left, 2, '405 must not touch the session');
  assert.ok(r['405'].delay_ms >= 48000);
});

test('QA-015 unscanned pairing QRs retry quickly (capped at 1 min) so the QR stays usable', () => {
  const { res } = run('qr_timeouts');
  const d = res.delays_seconds;
  assert.ok(Math.max(...d) <= 72, `max ${Math.max(...d)}`);
  assert.ok(d[0] <= 10);
});

test('QA-015 close + two restart calls leave a single pending start', () => {
  const { res } = run('no_stacking');
  assert.strictEqual(res.pending_restart_timers_after_close_plus_two_restarts, 1);
});

test('QA-016 the bot stays silent for chit-chat and while Django is down, and answers real shipments', () => {
  const { res } = run('noise', { REPLY_GAP_MS: '50' });
  assert.strictEqual(res.replies_after_ignored, 0, 'ignored chit-chat must not be answered');
  assert.strictEqual(res.replies_while_django_is_down, 0, 'no "please use the correct format" spam on Django errors');
  assert.deepStrictEqual(res.replies_after_success, ['Shipment recorded!']);
});

test('QA-016 replies are paced (never a burst) but none is lost, and the pending queue is bounded', () => {
  const { res } = run('reply_limit', { REPLY_GAP_MS: '120' });
  assert.deepStrictEqual(res.replies_to_5_messages_in_a_burst, ['help M0', 'help M1', 'help M2', 'help M3', 'help M4']);
  assert.ok(res.min_gap_ms_between_replies >= 100, `gap ${res.min_gap_ms_between_replies} ms`);
  assert.ok(res.sent_in_first_900ms_of_flood <= 9, `a flood must be spread out, sent ${res.sent_in_first_900ms_of_flood}`);
});

const FAST_RETRY = { OUTBOX_INTERVAL_MS: '150', OUTBOX_RETRY_BASE_MS: '100', REPLY_GAP_MS: '30' };

test('QA-018 messages sent while Django is down are queued on disk and delivered (once) when it returns', () => {
  const { res } = run('retry', FAST_RETRY);
  assert.strictEqual(res.replies_while_django_down, 0);
  assert.strictEqual(res.queued_on_disk, 3);
  assert.deepStrictEqual(res.delivered_message_ids, ['WA-ID-1', 'WA-ID-2', 'WA-ID-3']);
  assert.deepStrictEqual(res.replies_after_recovery, ['Recorded WA-ID-1', 'Recorded WA-ID-2', 'Recorded WA-ID-3']);
  assert.strictEqual(res.queue_empty_after_recovery, true);
});

test('QA-018 a 4xx answer from Django is final: no retry, no reply', () => {
  const { res } = run('retry_4xx', FAST_RETRY);
  assert.strictEqual(res.posts_for_a_400_answer, 1);
  assert.strictEqual(res.replies, 0);
});

test('QA-018 unsent messages survive a sidecar restart', () => {
  const { res } = run('outbox_restore', FAST_RETRY);
  assert.deepStrictEqual(res.delivered_message_ids, ['OLD-1']);
  assert.deepStrictEqual(res.replies, ['Recorded OLD-1']);
  assert.strictEqual(res.queue_file_after, 0);
});

test('QA-018 clearing the WhatsApp session keeps the unsent-message queue', () => {
  const { res } = run('clear_auth_keeps_queue', FAST_RETRY);
  assert.deepStrictEqual(res.files_after_clear_auth, ['_active_groups.json', '_outbox.json']);
});

test('QA-018 the WhatsApp message id is sent to Django (it deduplicates on it)', () => {
  const { res } = run('dedupe');
  assert.deepStrictEqual(res.message_id_sent_to_django, ['SAME-ID', 'SAME-ID']);
});

test('QA-020 the group filter fails closed', () => {
  const { res } = run('fail_closed');
  assert.strictEqual(res.a_nothing_loaded_yet, 0, 'before the list is known nothing is processed');
  assert.strictEqual(res.b_django_says_no_group_is_enabled, 0, 'an empty list means "listen nowhere", not "listen everywhere"');
  assert.strictEqual(res.c_enabled_group, 1);
  assert.strictEqual(res.d_disabled_group, 0);
  assert.strictEqual(res.e_unknown_group, 0);
  assert.strictEqual(res.f_direct_chat, 0, '1:1 chats are never dispatch messages');
  assert.strictEqual(res.g_django_unreachable_keeps_last_list, 1, 'a failed refresh must keep the last good list');
  assert.strictEqual(res.h_django_unreachable_still_ignores_others, 0, '...and must not widen it');
  assert.deepStrictEqual(res.cache_file, ['120363@g.us']);
});

test('QA-020 a restart while Django is down uses the enabled-groups copy on disk', () => {
  const { res } = run('cache_only');
  assert.strictEqual(res.processed_with_django_down_and_cache_on_disk, 1);
});
