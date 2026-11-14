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
