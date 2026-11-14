// QA mock of the WhatsApp sidecar REST API (no WhatsApp connection, zero dependencies).
// Lets us test Django -> sidecar behaviour: success, slow response, failure, no auth.
// Env: MOCK_DELAY_MS (default 0), MOCK_FAIL (1 => every call returns 500)
const http = require('http');
const delay = Number(process.env.MOCK_DELAY_MS || 0);
const fail = process.env.MOCK_FAIL === '1';
const calls = [];
const groups = [
  { jid: '111-222@g.us', name: 'QA Dispatch Group', participants: 5 },
  { jid: '333-444@g.us', name: 'QA Other Group', participants: 9 },
];
http.createServer((req, res) => {
  calls.push({ t: new Date().toISOString(), method: req.method, url: req.url, auth: req.headers.authorization ? 'present' : 'absent' });
  const send = (code, body) => { res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(body)); };
  setTimeout(() => {
    if (fail && req.url !== '/__calls') return send(500, { error: 'mock failure' });
    if (req.url === '/__calls') return send(200, calls);
    if (req.url === '/health') return send(200, { status: 'ok' });
    if (req.url.startsWith('/api/groups')) return send(200, { groups, total: groups.length });
    if (req.url.startsWith('/api/status')) return send(200, { sidecar_status: 'connected', auth_status: 'authenticated' });
    if (req.url.startsWith('/api/restart') && req.method === 'POST') return send(200, { status: 'restarting' });
    if (req.url.startsWith('/api/auth') && req.method === 'DELETE') return send(200, { status: 'auth_cleared' });
    return send(404, { error: 'not found' });
  }, delay);
}).listen(3001, () => console.log('mock sidecar on 3001 delay=%d fail=%s', delay, fail));
