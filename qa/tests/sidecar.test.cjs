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
