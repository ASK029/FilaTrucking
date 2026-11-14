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
  assert.deepStrictEqual(res.session_files_left_after_unauthenticated_calls, ['creds.json']);
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
  assert.strictEqual(r['401'].auth_files_left, 0, 'logged out: session wiped so a fresh QR can be issued');
  assert.ok(r['401'].delay_ms <= 3000);
  assert.ok(r['440'].delay_ms >= 60000, 'connection replaced: do not fight the other session');
  assert.ok(r['515'].delay_ms <= 1000);
  assert.strictEqual(r['515'].auth_files_left, 1, '515 must not touch the session');
  assert.strictEqual(r['405'].auth_files_left, 1, '405 must not touch the session');
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
