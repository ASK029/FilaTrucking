# QA task list (Phase 1 output, nothing fixed yet)

Ordered by priority. **Status of every task: `TODO – awaiting approval`.**
Root cause: **confirmed** = reproduced by a test/harness/experiment or read directly in code; **suspected** = plausible, needs production data (the diagnostics script supplies it).
Evidence paths are relative to `qa/`. Test names refer to `qa/tests/` (run: `python manage.py test qa.tests` from `FilaTrucking/` with `PYTHONPATH` = repo root). Effort: S ≤ ½ day, M ≈ 1–2 days, L > 2 days.
Secrets are never printed here: where a hard-coded secret is meant, only its file and line are given.

---

## CRITICAL

### QA-001 · WhatsApp sidecar cannot connect: stale pinned WhatsApp-Web version (HTTP 405)
- **Area:** WhatsApp · **Severity:** Critical (core feature down in production) · **Root cause:** confirmed
- **Steps:** start the sidecar with an empty `auth_data` → `Connection Failure … reason 405` every 3 s. Or run `node tools/wa_version_probe.cjs pinned` vs `latest`.
- **Expected:** a QR code is issued and pairing works. **Actual:** 405 loop (your production log, 2026-11-14 10:12–10:14).
- **Evidence:** `evidence/wa_probe_pinned.txt` (405), `wa_probe_latest.txt` and `wa_probe_6724_default.txt` (QR received); production log you pasted.
- **Fix:** upgrade `@whiskeysockets/baileys` to ≥ 6.7.24 and stop passing `version:` (or use `fetchLatestBaileysVersion()` with a fallback); rebuild; re-pair via QR on a dedicated number.
- **Files:** `sidecar/index.js:122`, `sidecar/package.json`, `sidecar/package-lock.json`.
- **Effort:** S · **Acceptance:** on the server a QR appears within 10 s of start; after scanning, `/health` reports connected; a test group message creates a shipment; 24 h without a 405 line.

### QA-002 · Sidecar REST API has no authentication and is exposed to the internet
- **Area:** Security · **Severity:** Critical · **Root cause:** confirmed
- **Steps:** `GET /api/qr-code`, `GET /api/logs`, `POST /api/restart`, `DELETE /api/auth` with no header against the sidecar (port 3001 or `https://<domain>/sidecar/…`).
- **Expected:** 401. **Actual:** 200; the QR is returned and the saved session files are deleted.
- **Evidence:** `evidence/sidecar_harness.txt` (`noauth_api`: `leaks_qr:true`, `session_files_left=[]`); `docker-compose.prod.yml` `ports: "3001:3001"`; `nginx.conf` `location /sidecar/`. *Not probed live (production is out of bounds); the diagnostics script checks it from localhost.*
- **Fix:** delete the host port mapping and the nginx `/sidecar/` block; add middleware requiring `Authorization: Bearer <secret>` on every route except an unauthenticated minimal `/health`; make the Django button calls (already send the header) the only caller.
- **Files:** `docker-compose.prod.yml`, `docker-compose.yml`, `nginx.conf`, `sidecar/index.js`.
- **Effort:** S · **Acceptance:** from outside the VPS `curl http://<ip>:3001/…` fails to connect and `/sidecar/…` is 404; inside the network a call without the header returns 401, with it 200; harness `noauth_api` shows 401s and session files intact.

### QA-003 · Django WhatsApp status/QR/logs/groups endpoints are public
- **Area:** Security/WhatsApp · **Severity:** Critical (pairing QR + internal data readable by anyone) · **Root cause:** confirmed
- **Steps:** unauthenticated `GET /shipments/api/whatsapp/{qr-code,status,logs,groups}/`.
- **Expected:** 401/403. **Actual:** 200 with data. Also `?limit=abc` → 500 (see QA-036).
- **Evidence:** failing tests `StatusEndpointsAreNotPublic.*` (4), `SiteSweep.test_every_route_requires_authentication`.
- **Fix:** apply `check_auth()` (login or Bearer) to the four GET views; drop `@csrf_exempt` where a session is the credential; make the browser JS send the CSRF header.
- **Files:** `shipments/api.py` (lines ~353-421), `templates/settings/whatsapp_settings.html`.
- **Effort:** S · **Acceptance:** those four tests pass; settings page still updates status when logged in.

### QA-004 · Shared secret is a public constant, and the env var that should override it is ignored
- **Area:** Security · **Severity:** Critical · **Root cause:** confirmed
- **Steps:** set `WHATSAPP_INGEST_SECRET=anything`, import `shipments.api` → still the default.
- **Expected:** secret from environment, startup fails if missing. **Actual:** `settings.py` never defines it; `api.py:19` falls back to a value that is also in `sidecar/index.js:13`, `.env.example:20`, `docker-compose.yml:47,78`, `docker-compose.prod.yml:36`. Anyone with the repo can POST fake shipments and call every control endpoint.
- **Evidence:** failing test `SecretHandling.test_ingest_secret_is_configurable_through_environment`; `prod_diagnostics.sh` section 4 tells you whether the server still uses the default (hash compare, no value printed).
- **Fix:** read `WHATSAPP_INGEST_SECRET` in `settings.py` with no default (raise `ImproperlyConfigured`); compare with `hmac.compare_digest`; remove literals from compose/example/code; generate a new random secret and put it only in the server `.env` (sidecar + Django); rotate.
- **Files:** `FilaTrucking/settings.py`, `shipments/api.py`, `sidecar/index.js`, `docker-compose*.yml`, `.env.example`.
- **Effort:** S · **Acceptance:** test passes; app refuses to start without the variable; old secret returns 401 in production.

### QA-005 · Stored XSS: WhatsApp-controlled shipment data is injected unescaped into a `<script>`
- **Area:** Security · **Severity:** Critical (any driver in the group can run script in the admin's session) · **Root cause:** confirmed
- **Steps:** a shipment whose location/booking/container contains `</script><script>…</script>` (reachable from a WhatsApp message `LOCATION:` line) → open `/invoices/<id>/lines/` as admin.
- **Expected:** text rendered inertly. **Actual:** the injected script executes (`window.__xss_location === true`), and the page's own script breaks (`SyntaxError`).
- **Evidence:** `evidence/ui_forms.json` (`xss_invoice_lines_page_executed: true`), `evidence/ui_crawl.json` (`/invoices/1/lines/` SyntaxError ×3); code `shipments/views.py:262` (`json.dumps`) + `invoice_add_lines.html:149` (`|safe`). Other templates escape correctly (customer-name payload did not execute).
- **Fix:** use `{{ shipment_data|json_script:"shipment-data" }}` and `JSON.parse(document.getElementById(...).textContent)`; also validate/limit ingest field charset and length.
- **Files:** `shipments/views.py`, `shipments/templates/shipments/invoice_add_lines.html`, `shipments/api.py`.
- **Effort:** S · **Acceptance:** a test posts a `</script>` payload and asserts the response contains no raw `</script><script>`; Playwright check shows no execution.

---

## HIGH

### QA-006 · Database root credential and insecure `SECRET_KEY` committed as defaults
- **Area:** Security · **Severity:** High (Critical if the repo is or was ever public: please tell me) · **Root cause:** confirmed
- **Evidence:** `settings.py:29` (insecure default key), `settings.py:107` (default `DATABASE_URL` contains a root password), `.env.example:13` (same). Default `ALLOWED_HOSTS` includes `*.ngrok-free.app`.
- **Fix:** no defaults for secrets (fail fast); rotate the MySQL root password and `SECRET_KEY` *together* with a plan for the Fernet-encrypted Motive/SMTP values (they are encrypted with a key derived from `SECRET_KEY`; rotating breaks decryption silently → re-enter them in Settings); app should use a non-root DB user (compose already creates `filatrucking`). Purge from git history if the repo has ever been shared.
- **Files:** `settings.py`, `.env.example`. **Effort:** S–M · **Acceptance:** `grep` finds no credentials in tracked files; app starts only with a complete `.env`.

### QA-007 · Shipment detail page crashes (500) when driver or vehicle is empty
- **Area:** Backend/Frontend · **Severity:** High (the page admins use to review and confirm every ingested shipment) · **Root cause:** confirmed
- **Steps:** open `/shipments/<id>/` for a shipment with no driver (driver is optional; QA-017 makes it empty for every WhatsApp shipment).
- **Expected:** page shows "Unassigned". **Actual:** `NoReverseMatch: driver_detail with arguments ('',)`.
- **Evidence:** 23 of 45 seeded shipments return 500 from the list (`evidence/ui_crawl_summary.txt` linkCheck); `SiteSweep` test; `evidence/` traceback via `tools/repro_shipment_detail.py`; template `shipment_detail.html:98,103`.
- **Fix:** wrap driver and vehicle links in `{% if shipment.driver %}…{% else %}Unassigned{% endif %}` (as `vehicle_detail.html` already does); add `404/500` templates (QA-043).
- **Files:** `shipments/templates/shipments/shipment_detail.html`. **Effort:** S · **Acceptance:** test renders detail for a shipment with neither; link check shows 0 × 500.

### QA-008 · Production compose has no Redis/Celery worker/beat: every scheduled job is dead
- **Area:** DevOps/Backend · **Severity:** High · **Root cause:** confirmed (config); runtime effect suspected → diagnostics section 2
- **Evidence:** `docker-compose.prod.yml` defines mysql, django, sidecar, nginx, certbot only; `settings.py:183` broker default `redis://127.0.0.1`. Monthly/yearly statements, recurring invoices, IFTA reminders, nightly GoMotive sync never run. Dashboard "Test Report"/"Sync Schedule" call `.delay()` with no broker → hangs/500 inside a gunicorn worker.
- **Fix:** add `redis`, `celery worker`, `celery beat` services (same image); set `CELERY_BROKER_URL`; or drop Celery and use cron + management commands; make dashboard buttons fail gracefully.
- **Files:** `docker-compose.prod.yml`, `FilaTrucking/views.py`. **Effort:** M · **Acceptance:** `celery inspect ping` OK; beat log shows the 03:00 sync firing; dashboard buttons return within 2 s.

### QA-009 · Monthly statement task crashes, reports the wrong period, and stores nothing
- **Area:** Backend · **Severity:** High · **Root cause:** confirmed
- **Evidence:** `tasks.py:46` uses undefined `driver_pay_total` → `NameError` (test `test_monthly_statement_task_computes`); beat fires on the 1st with no args, so both monthly and yearly tasks compute the **new, empty** period (tests `test_scheduled_*`: 2027 ≠ 2026); the task only returns a dict, so the PRD's "auto-generate draft statement" does not exist.
- **Fix:** compute previous month/year when called by beat; define driver pay; persist a `Statement` record (or generate the PDF) so there is something to review.
- **Files:** `shipments/tasks.py`, models/migration if persisted. **Effort:** M · **Acceptance:** the 3 tests pass; a statement row/PDF exists after the beat run.

### QA-010 · In-app CSV import never imports anything
- **Area:** Backend · **Severity:** High (milestone 8 marked this done) · **Root cause:** confirmed
- **Steps:** upload a valid CSV (`DATE,Category,Description,Amount`) at `/settings/import/`.
- **Expected:** rows imported. **Actual:** "No valid data rows found in CSV." always; `rows = list(reader)` exhausts the DictReader, the `for row in reader` loop sees nothing.
- **Evidence:** test `CsvImport.test_valid_csv_imports_rows`; `shipments/views.py:595-599`. Also: imported notes are `Imported: <description>`, but statements bucket categories by `Imported: <CATEGORY LABEL>` (see QA-037).
- **Fix:** iterate `rows`; align the note/category convention (store the category and stop matching on notes). **Files:** `shipments/views.py`, `FilaTrucking/views.py`. **Effort:** S · **Acceptance:** test passes; imported FUEL row shows in the FUEL column.

### QA-011 · Creating an invoice can leave an empty orphan invoice and discards the user's input on error
- **Area:** Backend/UX · **Severity:** High (financial records) · **Root cause:** confirmed
- **Evidence:** tests `InvoiceCreate.test_invalid_line_item_does_not_leave_an_orphan_invoice` (1 ≠ 0) and `…keeps_what_the_user_typed`; `views.py:92-93` saves the invoice before validating the formset, no `transaction.atomic`, and re-renders with a fresh empty formset.
- **Fix:** validate form + formset together, save in one transaction, re-render the bound formset. **Files:** `shipments/views.py`. **Effort:** S–M · **Acceptance:** both tests pass.

### QA-012 · "Add Manual Item" on the invoice form throws a JavaScript error
- **Area:** Frontend · **Severity:** High · **Root cause:** confirmed
- **Steps:** `/invoices/create/` → "Add Manual Item". **Expected:** new row posted with the invoice. **Actual:** `TypeError: Cannot set properties of null` — JS uses `id_form-TOTAL_FORMS` / `form-N-*` names, Django renders `id_line_items-TOTAL_FORMS` / `line_items-N-*`; new rows would be ignored on submit even without the error.
- **Evidence:** `evidence/ui_forms.json` (`invoice_form_management_form_ids`, `invoice_add_manual_item_js_errors`); `invoice_form.html:205-265`.
- **Fix:** use the formset prefix (`{{ formset.prefix }}`) or Django's `empty_form` template; same for remove-row. **Files:** `shipments/templates/shipments/invoice_form.html`. **Effort:** S–M · **Acceptance:** Playwright adds a row, submits, row saved.

### QA-013 · Re-sending a paid invoice resets it to "sent" and removes it from revenue
- **Area:** Backend · **Severity:** High (money reports) · **Root cause:** confirmed
- **Evidence:** test `InvoiceEmail.test_email_does_not_downgrade_a_paid_invoice` (`('sent', None)` ≠ `('paid', date)`); `views.py:396-398` sets `status="sent"`, `paid_at=None` unconditionally; statements count only paid invoices by `paid_at`.
- **Fix:** only move `draft → sent`; never touch `paid_at`. **Files:** `shipments/views.py`. **Effort:** S · **Acceptance:** test passes.

### QA-014 · Emailing/PDF actions run on GET and synchronously
- **Area:** Backend/Security · **Severity:** High · **Root cause:** confirmed (GET) / suspected (timeouts)
- **Evidence:** test `test_sending_email_requires_post` (a GET sent 1 email); `invoice_email` has no method check; SMTP has no `EMAIL_TIMEOUT` → a slow SMTP blocks a sync gunicorn worker until the 30 s kill.
- **Fix:** POST-only + CSRF, set `EMAIL_TIMEOUT`, optionally send via Celery. **Files:** `shipments/views.py`, template buttons, `settings.py`. **Effort:** S · **Acceptance:** test passes; buttons are forms.

### QA-015 · Sidecar: crash path, duplicate sockets, interval leak, endless reconnect
- **Area:** WhatsApp · **Severity:** High · **Root cause:** confirmed (behaviour) / suspected (that the crash caused your 3 restarts)
- **Evidence:** `evidence/sidecar_harness.txt` — `crash`: process exit 1 (unhandled rejection from `sendMessage` inside `catch`, `index.js:283`); `restart_dup`: one restart → 2 sockets open; `interval_leak`: 4 opens → 4 permanent 30 s intervals (`index.js:178`); `reconnect_loop`: 5 attempts/13 s, fixed 3.0 s, no cap.
- **Fix:** single reconnect function guarded by a flag; exponential backoff (3 s → 5 min) with jitter; per-status-code policy; `clearInterval` on close; wrap every `sendMessage` in try/catch; `process.on('unhandledRejection')` that logs and keeps running; `end()` the old socket before creating a new one.
- **Files:** `sidecar/index.js`. **Effort:** M · **Acceptance:** all four harness scenarios report the corrected numbers (exit 0, 1 socket, 1 interval, growing gaps).

### QA-016 · Bot replies to every chat message in the dispatch group
- **Area:** WhatsApp · **Severity:** High (spam + account-ban risk + noise) · **Root cause:** confirmed
- **Evidence:** harness `noise` (`"⚠️ System flagged the shipment…"` sent for "ok thanks boss"); tests `WA-NOISE` ×2 (Django returns `flagged` and stores an error row).
- **Fix:** Django returns `{"status":"ignored"}` (no reply, no error row) unless the text looks like a shipment attempt (≥ 2 shipment tokens); sidecar replies only on `success`, and for `flagged` only to the sender with a short message; rate-limit replies (≤ 1 per few seconds).
- **Files:** `shipments/api.py`, `sidecar/index.js`. **Effort:** S–M · **Acceptance:** both tests pass; harness `noise` shows zero replies.

### QA-017 · WhatsApp shipments always lack a driver and ignore the customer's default rate
- **Area:** WhatsApp/Backend · **Severity:** High (causes QA-007 and $0 invoice lines) · **Root cause:** confirmed
- **Evidence:** tests `WA-DRIVER` (`None ≠ Driver`), `WA-RATE` (`0.00 ≠ 400.00`); `api.py:268` `phone_number__icontains=<jid>`; `api.py:306` `amount = … else 0`.
- **Fix:** normalise sender JID → digits → E.164 and compare exactly; map `@lid` ids via stored number once known; use `customer.default_rate` when no rate given; keep amount `NULL`/flag instead of 0.
- **Files:** `shipments/api.py`. **Effort:** S–M · **Acceptance:** both tests pass.

### QA-018 · Ingest robustness: duplicates, malformed input, timezone, no retry
- **Area:** WhatsApp/Backend · **Severity:** High · **Root cause:** confirmed
- **Evidence:** tests `WA-DEDUPE` (2 ≠ 1), JSON-array body and non-string text → 500 (2 tests), `WA-TZ`; harness `dedupe` (2 POSTs); sidecar posts once with a 10 s timeout and then only replies with format help (`index.js:227-284`), so failures during a Django restart lose the message.
- **Fix:** sidecar sends WhatsApp message id (+ jid/timestamp); Django unique constraint → idempotent; validate payload types (400); convert message time to the business timezone (`America/Chicago`); sidecar retries with backoff and a small persisted queue.
- **Files:** `shipments/api.py`, `shipments/models.py` (+ migration), `sidecar/index.js`. **Effort:** M · **Acceptance:** the 5 tests pass; stop Django for 30 s, send 3 messages → 3 shipments appear after it returns.

### QA-019 · "Sync groups" re-enables every group; QR and last error never cleared
- **Area:** WhatsApp · **Severity:** High · **Root cause:** confirmed
- **Evidence:** tests `WA-SYNC-RESET`, `WA-STALE-QR`, `WA-STALE-ERR`; `api.py:571` forces `"is_active": True`; `api.py:512-515` only overwrite when truthy.
- **Fix:** don't touch `is_active` on existing groups (default False for new); clear `qr_code_data`/`last_error` on `connected`; do not delete groups on "Clear auth" unless the user asks. **Files:** `shipments/api.py`. **Effort:** S · **Acceptance:** the 3 tests pass.

### QA-020 · Group filter fails open: bot listens/replies everywhere when the list is empty or Django is unreachable
- **Area:** WhatsApp · **Severity:** High · **Root cause:** suspected (code reading, `index.js:213-220, 333-337`)
- **Fix:** fail closed (ignore everything until a non-empty active list has been loaded); ignore 1:1 chats unless explicitly allowed; cache the last good list on disk. **Files:** `sidecar/index.js`. **Effort:** S · **Acceptance:** harness: with Django down and no cache, a group message produces no POST and no reply.

### QA-021 · Known-vulnerable dependencies (Python and Node)
- **Area:** Security · **Severity:** High · **Root cause:** confirmed (tool output)
- **Evidence:** `evidence/pip_audit.txt`: 167 advisories in 10 packages (Django 6.0.1 → fix ≥ 6.0.8 [27], Pillow 12.1.1 → 13.x [60], cryptography 44.0.1 → ≥ 46/50 [6, used for the Fernet-encrypted settings], urllib3, sqlparse, DRF, requests, idna, certifi, pygments). `evidence/npm_audit_summary.txt`: 11 (2 critical: **Baileys ≤ 6.7.21**, protobufjs; axios, ws, form-data … high). Exposure varies (many only matter if untrusted input reaches them) but Django and Baileys are in the hot path.
- **Fix:** upgrade Django to the latest 6.0.x, Pillow, cryptography etc.; Baileys per QA-001; `npm audit fix`; add `pip-audit`/`npm audit` to CI. **Files:** `requirements*.txt`, `sidecar/package*.json`. **Effort:** M (regression-test after upgrade) · **Acceptance:** both audits clean or documented exceptions.

### QA-022 · Sidecar image build is not reproducible and partly broken
- **Area:** DevOps · **Severity:** High · **Root cause:** confirmed
- **Evidence:** 2,170 files of `sidecar/node_modules` are committed (Windows-built, 73 top-level packages, **no `express`**) and, because `sidecar/` has no `.dockerignore`, `COPY . .` overlays them on the image's `npm install` result; `package-lock.json` says `@whiskeysockets/baileys: "latest"` and lacks `express`; `npm install` is used instead of `npm ci`; `patches/baileys+6.7.0.patch` targets the wrong package and version (clean-install run: *"Patch file found for package baileys which is not present"*, `qa/tools/audit/`), never applies; Chromium (~400 MB) installed and unused.
- **Fix:** `git rm -r --cached sidecar/node_modules`, add `sidecar/.dockerignore` + root `.gitignore` entry, regenerate lockfile, `npm ci --omit=dev`, delete the patch and Chromium, pin base image. **Files:** `sidecar/*`, `.gitignore`. **Effort:** S · **Acceptance:** clean clone builds; `docker run … npm ls` matches the lockfile.

### QA-023 · Uploaded files (driver licences, receipts) are served publicly by nginx with guessable names
- **Area:** Security · **Severity:** High · **Root cause:** confirmed (name) / suspected (live exposure)
- **Evidence:** test `SecretsAndUploads.test_uploaded_documents_get_unguessable_names`; `nginx.conf` `location /media/ { alias /media/; }` with no auth; originals keep their file name (`driver_documents/license.pdf`); no upload size/type validation.
- **Fix:** random file names (`uuid4` in `upload_to`), serve documents through an authenticated Django view or nginx `auth_request`/`X-Accel-Redirect`, validate type/size. **Files:** `drivers/models.py`, `shipments/models.py`, `nginx.conf`. **Effort:** M · **Acceptance:** test passes; anonymous GET of a document URL → 302/401.

### QA-024 · Real business/PII data committed to git and baked into the image
- **Area:** Security · **Severity:** High (unknown repo visibility) · **Root cause:** confirmed
- **Evidence:** tracked: `docs/2025 STATMENTS*.csv/xlsx`, `docs/IFTA2025 2ND.xlsx`, `gomotive_resp.json` (vehicle data), `FilaTrucking/media/driver_documents/*` (a driver timesheet), vehicle screenshots; root `Dockerfile` `COPY FilaTrucking/` includes `media/`.
- **Fix:** remove from tracking, add to `.gitignore`/`.dockerignore`, purge history if the repo was ever public/shared, keep samples anonymised. **Effort:** S · **Acceptance:** `git ls-files` shows none; image has no `media/`.

### QA-025 · Django published on host port 8888 (bypasses nginx/TLS); containers run as root; no healthchecks
- **Area:** Security/DevOps · **Severity:** High · **Root cause:** confirmed (config) / suspected (firewall state)
- **Evidence:** `docker-compose.prod.yml` `ports "8888:8000"`; no `user:`; only MySQL has a `healthcheck`; no `mem_limit` in prod.
- **Fix:** remove `ports` for django/sidecar (nginx reaches them on the compose network), add `HEALTHCHECK`s, `user: 1000`, `read_only` where possible, log rotation. Also check the OCI security list. **Effort:** S–M · **Acceptance:** `ss -ltn` shows only 22/80/443 public; `docker ps` shows `healthy`.

### QA-026 · nginx is never reloaded after certbot renews: certificate-expiry outage ahead
- **Area:** DevOps · **Severity:** High · **Root cause:** suspected (config reading; diagnostics section 8 confirms)
- **Evidence:** certbot service is a `certbot renew` loop with no `--deploy-hook`; nginx loads certificates once.
- **Fix:** `--deploy-hook "docker kill -s HUP fila-nginx"` (or a nightly `nginx -s reload` cron), add an expiry alert. **Effort:** S · **Acceptance:** after `certbot renew --force-renewal` (staging) the served certificate's `notAfter` changes without manual action.

### QA-027 · No MySQL backups
- **Area:** DevOps · **Severity:** High (data loss) · **Root cause:** suspected (no backup service in compose; diagnostics section 6 checks the host)
- **Fix:** nightly `mysqldump | gzip` to a dated file + off-host copy (OCI Object Storage), retention, a tested restore. **Effort:** M · **Acceptance:** a restore into a scratch DB reproduces row counts.

---

## MEDIUM

### QA-028 · End-of-life base images
- **Area:** DevOps · **Root cause:** confirmed (versions) · Node 20 / Debian 11 bullseye (sidecar), `mysql:8.0` all reached vendor end-of-life in 2026; `python:3.12-slim` is a floating tag (it already moved to Debian 13 during my build). **Fix:** Node 22/24-slim, MySQL 8.4 LTS (test upgrade on a copy), pin tags/digests. **Effort:** M · **Acceptance:** images on supported releases, builds reproducible.

### QA-029 · Production security flags: SSL redirect/secure cookies depend on `.env`
- **Area:** Security · **Root cause:** suspected for production
- **Evidence:** with the `.env` present on this machine, `check --deploy` reports `security.W008/W012/W016` (`evidence/check_deploy.txt`) because `SECURE_SSL_REDIRECT`, `SESSION_COOKIE_SECURE`, `CSRF_COOKIE_SECURE` resolve to `False`; `settings.py` defines the production block twice. The diagnostics script prints the real values on the server.
- **Fix:** default to secure when `DEBUG` is false; delete the duplicate block; `SECURE_PROXY_SSL_HEADER` only behind nginx; fix wrong comment (`SESSION_COOKIE_AGE` is 60 min). **Effort:** S · **Acceptance:** `check --deploy` shows 0 warnings with the production env.

### QA-030 · No login throttling
- **Area:** Security · **Evidence:** test `test_login_is_rate_limited` (15 wrong passwords, never limited). **Fix:** `django-axes` or nginx `limit_req` on `/auth/login/`; consider 2FA for the single admin. **Effort:** S.

### QA-031 · System Settings page writes stored secrets into the HTML
- **Area:** Security · **Evidence:** test `test_system_settings_page_does_not_echo_stored_secrets` (2 secrets rendered; `PasswordInput(render_value=True)`). **Fix:** render empty with "leave blank to keep"; only update when non-empty. **Files:** `shipments/forms.py:104-141`. **Effort:** S.

### QA-032 · Mobile usability: tap targets and clipped columns
- **Area:** UI-UX · **Evidence:** at 375 px **545 of 608** interactive elements (90 %) are smaller than 44×44 px, violating the project's own rule (`AGENTS.md` §7.5; worst `/shipments/` 95/95, `/expenses/` 45/45); horizontal page overflow on `/invoices/1/` (663 px), `/shipments/2/` (400 px), `/invoices/1/lines/`, and at 768 px on `/vehicles/`; shipment list hides customer/amount/status behind a horizontal scroll (see `screenshots/shipments_375.png`). **Fix:** `min-h-[44px]` on controls; card layout instead of table below `md`. **Effort:** M.

### QA-033 · Accessibility
- **Area:** UI-UX · **Evidence:** axe-core at 1440 px: colour-contrast on 41 pages (521 nodes), missing form labels 47 nodes/7 pages (critical), unnamed selects 17, unnamed links 6, image without alt, definition-list structure, duplicate/absent `<main>` landmarks on 2 pages, no skip-to-content link; 31 distinct titles for 43 pages. Focus indicators are present (14/14 tab stops). **Fix:** raise `text-muted` contrast, label every input (Django `label_tag`), titles per page. **Effort:** M.

### QA-034 · N+1 queries and unpaginated lists
- **Area:** Backend · **Evidence:** failing tests: expense list 10 → 44 queries (3 → 30 rows), invoice 7 → 24, shipment 6 → 33, vehicle 6 → 33, maintenance 6 → 33; shipments/drivers/vehicles/customers lists have no pagination; maintenance list sorts all rows in Python. **Fix:** `select_related`, `paginate_by`, DB-side ordering. **Effort:** S–M.

### QA-035 · Frontend delivery: Tailwind Play CDN in production, floating Alpine, missing plugin, no SRI
- **Area:** Frontend · **Evidence:** console on 123 page loads: "cdn.tailwindcss.com should not be used in production"; 492 × "Alpine Warning: x-collapse … Collapse plugin" (`evidence/ui_crawl_summary.txt`); `alpinejs@3.x.x`; QR lib from cdnjs without integrity. Page styling depends on third parties at runtime (outage = unstyled site) and on in-browser compilation (slow first paint). **Fix:** build Tailwind once (CLI) into `static/`, vendor/pin Alpine + collapse plugin, add SRI. **Effort:** M.

### QA-036 · Query-string parameters cause 500s
- **Area:** Backend · **Evidence:** `?month=13`, `?month=abc`, `?year=99999` on the monthly statement → 500 (3 subtests); WhatsApp logs `?limit=abc` → 500 and no upper bound. **Fix:** validate and return 400/clamp. **Effort:** S.

### QA-037 · Statement category breakdown matches free text in notes, not the category field
- **Area:** Backend · **Evidence:** `FilaTrucking/views.py:174-196` (`notes__icontains="Imported: <label>"`); manually entered expenses and web-imported rows never appear in the CSV-style columns. **Fix:** group by `category`. **Effort:** S–M.

### QA-038 · Invoice lifecycle gaps: removed lines keep shipments "invoiced"; a shipment can be on two invoices
- **Area:** Backend · **Evidence:** tests `test_removing_a_line_returns_the_shipment_to_confirmed`, `test_shipment_cannot_be_invoiced_twice`. **Fix:** signal/`formset.save` hook to restore status; validate shipment status `confirmed` and uniqueness. **Effort:** S–M.

### QA-039 · Destructive cascades and lost history
- **Area:** Backend/Data · **Evidence:** `Shipment.customer` and `Invoice.customer` `CASCADE`, `Vehicle` → IFTA/Maintenance `CASCADE` (test `test_deleting_a_vehicle_keeps_ifta_history` fails), deleting a driver nulls `Expense.driver` (driver-pay history lost). **Fix:** `PROTECT`, soft-delete/“inactive”. **Effort:** M.

### QA-040 · GoMotive vehicle sync 500 and un-editable service date
- **Area:** Backend · **Evidence:** `vehicles/views.py` reads removed field `vehicle.name` → 500 when the vehicle has no GoMotive id (test `test_sync_for_vehicle_without_gomotive_id…`); `Maintenance.date` is `auto_now_add` (test `…service_date_is_editable`) so historical services cannot be entered. **Effort:** S.

### QA-041 · Dev docker-compose is inconsistent
- **Area:** DevOps · **Evidence (by reading; stack not run):** Django env `WHATSAPP_SIDECAR_URL=http://sidecar:3001` but the service is `whatsapp-sidecar`; the auth volume is mounted at `/app/sidecar/auth_info_baileys` while the code writes `auth_data`; prod/dev sidecar service names differ. **Fix:** align names and paths. **Effort:** S.

### QA-042 · Recurring-invoice task aborts on a shipment without amount and ignores per-customer schedules
- **Area:** Backend · **Evidence:** test `test_shipment_without_amount_does_not_abort_the_whole_run` (`IntegrityError`, one bad shipment stops all customers); runs nightly for the previous month instead of the PRD's per-customer schedule. **Effort:** M.

### QA-043 · The existing test suite is red and there is no CI
- **Area:** Backend/Process · **Evidence:** 14 existing tests: 6 pass, 8 fail (`evidence/existing_tests.txt`): `customer_id=None` fixtures, removed `Vehicle.name/engine_number`, GoMotive response-shape drift. No CI, no coverage for views. **Fix:** repair/replace; adopt `qa/tests`; GitHub Actions running tests + audits. **Effort:** M.

### QA-044 · Time zone
- **Area:** Backend · **Evidence:** `TIME_ZONE = "UTC"`, `date.today()` in tasks and dashboard; Illinois business → "this month" and message dates roll over at 7–6 pm local. **Fix:** `America/Chicago` + `timezone.localdate()`. **Effort:** S.

---

## LOW

### QA-045 · WhatsApp logs: unbounded growth and PII retention
- **Evidence:** every sidecar status ping writes a `WhatsAppLog` row (test `WA-LOG-GROWTH`); 3 s loop ≈ 1,200 rows/h; `WhatsAppMessage.raw_text` + phone numbers kept forever; no Docker log rotation. **Fix:** prune job, `max-size` log options, log only state changes. **Effort:** S.

### QA-046 · No branded 404/500 pages (and no skip link, double-submit guard)
- **Evidence:** no `404.html`/`500.html` in `templates/` (default unstyled pages violate `AGENTS.md` §7); double click on submit buttons has no client guard (one real click was not enough to reproduce a duplicate, so this is preventive). **Effort:** S.

### QA-047 · Dashboard copy/formatting
- **Evidence:** `screenshots/home_375.png`: "-15000 miles remaining" for an overdue service, "$2457" without thousands separator, stale errors/QR shown on the WhatsApp page (QA-019). **Effort:** S.

### QA-048 · Small parser gap: values shorter than 2 characters are dropped
- **Evidence:** positional parser skips tokens with `len ≤ 1` (`api.py:118`), so a 1-digit seal makes the message "missing fields". **Effort:** S.
