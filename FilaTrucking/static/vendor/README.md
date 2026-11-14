# Vendored front-end libraries (QA-035)

These used to be loaded from third-party CDNs on every page view (`alpinejs@3.x.x` = whatever the newest 3.x was that
day, no integrity check; a CDN outage left the site unusable). They are pinned and served from this server.

| File | Package | Version | License |
|---|---|---|---|
| `alpine.min.js` | alpinejs | 3.14.9 | MIT |
| `alpine-collapse.min.js` | @alpinejs/collapse | 3.14.9 | MIT (must load BEFORE alpine.min.js) |
| `qrcode.js` | qrcodejs (davidshimjs) | 1.0.0 | MIT |

Update: `npm pack <package>@<version>`, copy `package/dist/cdn.min.js` (Alpine) or `package/qrcode.js`, bump the table.
The Tailwind stylesheet `../css/app.css` is built from `frontend/` (`cd frontend && npm ci && npm run build`).
