# FilaTrucking QA report

> **Phase 2 (fixes) is finished on branch `qa-fixes`** and nothing has been deployed. Sections 1 to 7 below are the original
> Phase 1 report, kept as written; this block says what happened since. Server steps: `qa/DEPLOYMENT.md`.

## Phase 2 results

| | Phase 1 | Now |
|---|---|---|
| Tasks | 48 open | **49** (QA-049 found during the fixes): **43 DONE**, **6 PARTIAL** (what is left needs a decision or access only you have), 0 open |
| Tests | 69 QA tests (42 failing = confirmed defects) + 14 existing (8 failing) | **269 tests, 0 failing**, on SQLite and on a real **MySQL 8.0.45** (0 skipped there), plus **23 sidecar behaviour tests** (`node --test`) |
| `pip-audit` / `npm audit` | 167 advisories in 10 packages / 11 (2 critical) | **0 / 0** (sidecar and frontend build tooling) |
| `manage.py check --deploy` | 3 warnings with the local `.env` | 0 warnings with the production-style environment |
| Browser crawl, 43 pages x 375 px | 1 page answering 500, 1 JavaScript error, 207 console warnings, 545 of 608 tap targets under 44 px, 65 unlabeled controls, 31 distinct page titles | **0** pages answering 500, **0** JavaScript errors, **1** console message (the intentional 404 test page), **79** of 622 small targets, **0** unlabeled controls, **42** distinct titles |
| Commits | | one per task, subject `QA-0xx: ...`, with its tests (52 commits on top of your `0bfa602`) |

### What I verified for real (not just wrote)

* **WhatsApp 405 root cause** (Phase 1) and the whole sidecar behaviour with a Node harness that runs the real `index.js`
  with Baileys/Django stubbed: crash path, one socket per restart, backoff by status code, fail-closed group filter, retry
  queue that survives a restart, secret enforcement on every route. No message was sent to anyone.
* **MySQL 8.0.45 container:** all migrations apply from scratch; the whole suite passes; a seeded database (45 shipments,
  4 invoices) was dumped by `deploy/backup_mysql.sh` and restored by `deploy/restore_mysql.sh` with identical row counts;
  a wrong password leaves no partial dump.
* **Real browser (Playwright/Chromium):** the stored-XSS payload no longer executes, "Add Manual Item" adds a form row and
  posts it, the Logout button signs out, a double click creates one shipment, keyboard focus is visible, the mobile menu
  opens, the 404 is the branded page. Before/after screenshots: `qa/screenshots` (Phase 1) vs `qa/screenshots_after`;
  a pixel comparison of the 116 unchanged-URL pairs showed the median page identical, and caught a real regression
  (a multi-line `{# #}` template comment printed my notes at the top of every page, fixed before it was committed).
* `docker compose config` for both compose files, `nginx -t` in the real `nginx:alpine` image, `collectstatic` with hashed names.

### Sidecar image: built and run (correction: I first reported it as stopped)

The sidecar build finished on its own. I ran the image locally with a dummy secret and no Django: it started as uid 1000 (not root),
fetched the current WhatsApp-Web version, **and WhatsApp accepted it and issued a pairing QR** (no QR scanned, nothing sent);
`/health` answered `ok`, `/api/status` without the secret answered 401, `auth_data` was owned by `node`. This is the end-to-end proof of QA-001/002/022/025
for the sidecar. The container and image were deleted afterwards.

### What I could NOT verify, and what you must do

| Item | Why | You |
|---|---|---|
| WhatsApp re-pairing with the new Baileys on the server | needs your QR scan and the server | `qa/DEPLOYMENT.md` section 7 |
| WeasyPrint 68.1 -> 70.0 invoice/statement **PDFs** | native libraries are missing on this Windows machine, PDFs were stubbed | open one invoice PDF after deploying |
| Django Docker image build | I stopped it after about 25 minutes: the Debian mirror was still on the first apt step at a few KB/s, as in Phase 1 (not a build failure; `qa/evidence/docker_build_django.log`). Dockerfile and compose files were checked statically, by tests, and with `docker compose config` / `nginx -t`. (**The sidecar image did build and was run, see below.**) | `docker compose ... build` on the server, then `docker compose ps` should show every service healthy |
| The GitHub Actions workflow | it has not run on GitHub; every step was run locally | push and look |
| Rotating the leaked secrets, DB passwords, firewall, off-server backups, purging the public git history | server/GitHub actions, or destructive | `qa/DEPLOYMENT.md` sections 0, 4, 8, 9 |

### The 6 PARTIAL tasks

* **QA-024** public history still contains the committed business data (needs a force-push you must decide on).
* **QA-027** backups are automatic and the restore is rehearsed, but the copy to another machine needs your credentials.
* **QA-028** MySQL 8.0 -> 8.4 needs a rehearsed maintenance window (runbook in `deploy/README.md`).
* **QA-032 / QA-033** the phone layout and accessibility are much better (numbers above) but I did not run a screen reader, 24 px checkboxes in table cells remain, and two statement pages overflow by 7 px at 768 px.
* **QA-042** the per-customer recurring-invoice schedule is a feature that needs your decisions; the job itself is now robust and stays opt-in.

### Corrections to Phase 1

* QA-015: production shows `RestartCount=3` with exit code 0 and no unhandled rejections, so the sidecar crash path did **not** cause those restarts (I had suspected it). The defect is real and fixed, the causal claim is withdrawn.
* QA-049 (Logout returned 405) was already visible in my own Phase 1 browser evidence (`logout_via_GET_status = 405`) and I did not turn it into a task.
* The prod diagnostics showed the bot has probably been offline since the container restart on 2026-06-09, not only this week (`WHATSAPP_INVESTIGATION.md` section 9).

---

# Phase 1 report (original, unchanged below)


Date: 2026-11-14 · Scope: whole repo at commit `58fb468` · Production was **not** touched.

## 1. Executive summary

The site's WhatsApp feature is **down in production and cannot recover on its own**: your log shows the sidecar looping on `405 Connection Failure`. I reproduced it: the WhatsApp-Web version hard-coded in `sidecar/index.js:122` is rejected by WhatsApp, while the current version (and Baileys 6.7.24's default) is accepted and issues a QR code. Fixing that is a half-day change.

That is the tip of a larger problem. The same sidecar, as deployed, has **no authentication on its REST API, and that API is reachable from the internet** (host port 3001 and nginx `/sidecar/`): anyone can read the pairing QR or delete the saved WhatsApp session with one request. Django mirrors the QR and status on public routes, the shared secret is a constant in the repo (and Django ignores the environment variable meant to replace it), and a stored-XSS hole lets any driver in the WhatsApp group run script in the admin's browser through the invoice screen.

Beyond WhatsApp, a lot of *core business screens are broken*: half of the shipments crash their detail page (500), the invoice "Add Manual Item" button throws a JavaScript error, the CSV importer never imports, the monthly statement task crashes, re-emailing a paid invoice silently removes it from revenue, and the production compose file has no Celery/Redis so no scheduled job (statements, recurring invoices, GoMotive sync) ever runs. On the operations side there are no database backups in the compose stack, nginx is never reloaded after certificate renewal, and base images (Node 20, Debian 11, MySQL 8.0) are past end-of-life.

What works: login/permissions on all normal pages, shipment/driver/customer/vehicle CRUD, invoice creation happy path, PDF-less invoice lifecycle, IFTA entry upsert, migrations (`makemigrations --check`: no drift), 404s for unknown objects, escaping of ordinary template output (a `<script>` customer name did not execute), keyboard focus indicators, mobile menu.

## 2. Counts

48 tasks in `TASKS.md`.

| Severity | WhatsApp | Security | Backend | Frontend | UI-UX | DevOps | **Total** |
|---|---|---|---|---|---|---|---|
| Critical | 1 | 4 | – | – | – | – | **5** |
| High | 6 | 4 | 6 | 1 | – | 5 | **22** |
| Medium | – | 3 | 9 | 1 | 2 | 2 | **17** |
| Low | 2 | – | – | – | 2 | – | **4** |
| **Total** | **9** | **11** | **15** | **2** | **4** | **7** | **48** |

Each task is labelled *confirmed* (reproduced or read directly in code) or *suspected* (needs production data). Of the 48, 7 are suspected in whole or in part (QA-015's link to your restarts, QA-020, QA-023 live exposure, QA-025 firewall state, QA-026, QA-027, QA-029 for production); the rest are confirmed.

## 3. Top 5 risks

1. **WhatsApp ingestion is offline** (QA-001). Drivers' messages are not being captured at all right now; admin must re-enter shipments by hand until it is fixed.
2. **Anyone on the internet can wipe or hijack the WhatsApp session** (QA-002/003/004). Unauthenticated sidecar API via port 3001 and `/sidecar/`, unauthenticated Django QR/status routes, and a public shared secret. This is also the most likely way the session was lost in the first place (unproven; see diagnostics).
3. **Money-path defects** (QA-007, 009–013, 017): ingested shipments lack a driver and a rate; their detail page crashes; invoices can be orphaned or mis-statused; paid invoices drop out of revenue when re-sent; scheduled statements crash and compute the wrong period. Financial reports can be silently wrong.
4. **Stored XSS plus committed secrets** (QA-005, 006): a driver-controlled message can execute script in the admin session; a database root password and insecure `SECRET_KEY` are defaults in committed code.
5. **No safety net** (QA-008, 026, 027, 028): no scheduler container, no backups, certificate renewal that never reaches nginx, end-of-life base images, 167 + 11 known dependency advisories (Django 6.0.1, Baileys 6.7.21 among them).

## 4. What was tested (and how)

| Area | Method | Result / evidence |
|---|---|---|
| Existing tests | `manage.py test` on SQLite | 14 tests: 6 pass, 8 fail (stale fixtures) · `evidence/existing_tests.txt` |
| Django checks | `check`, `check --deploy`, `makemigrations --check`, `showmigrations` | no issues; 3 deploy warnings caused by `.env` flags; no migration drift · `evidence/check_deploy.txt` |
| New backend/security tests | `qa/tests/` (69 test cases, written to assert *correct* behaviour) | 42 fail = 42 confirmed defects (each cites its task); 26 pass = behaviour verified; 1 skipped (MySQL-only length test) · `evidence/qa_suite_sqlite.txt` |
| Every route | resolver-driven sweep: anonymous (must be blocked) and logged-in (must not 500), 77 routes | 4 public WhatsApp GETs, 1 crashing page |
| N+1 | query counting at 3 vs 30 rows on 5 list pages | all 5 grow linearly |
| Sidecar | real `index.js` under a Node harness with Baileys/axios stubbed, 7 scenarios | 6 defects reproduced · `evidence/sidecar_harness.txt` |
| WhatsApp acceptance | 3 registration-only connections from my machine (no QR scanned, no message) | pinned version 405; latest and 6.7.24 accepted · `evidence/wa_probe_*.txt` |
| Browser | Playwright/Chromium: 43 pages × 3 viewports (375/768/1440), full-page screenshots (`screenshots/`, 135 files), console/network/overflow/tap-target/label checks, axe-core, link check (208 links), plus interactive form, XSS, JS-error, keyboard and mobile-menu tests | `evidence/ui_crawl_summary.txt`, `ui_forms.json` |
| Dependencies | `pip-audit` (isolated venv), `npm audit` | 167 Python advisories in 10 packages; 11 npm (2 critical) |
| DevOps/security review | compose, Dockerfiles, nginx, settings, committed files | tasks QA-002…029 |
| Server diagnostics | `prod_diagnostics.sh` (syntax-checked, **not run**: needs the server) | you run it; see `WHATSAPP_INVESTIGATION.md` §8 |

Reproduce: `python manage.py test qa.tests` from `FilaTrucking/` with `PYTHONPATH=<repo root>` and the throw-away env shown at the top of `qa/tools/*`; Node tools: `cd qa/tools && npm i` then `node ../tests/sidecar_harness.cjs <scenario>` (needs `NODE_PATH=qa/tools/node_modules`), `BASE=http://127.0.0.1:18080 node ui_crawl.cjs`. `qa/seed_fake_data.py` creates the fake data (it refuses to run on non-SQLite). `qa/docker-compose.override.yml` + `qa/mock_sidecar/` + `qa/.env.qa` define the local Docker stack (fake credentials, alternate ports).

## 5. What could not be tested, and why

- **The full Docker Compose stack.** I wrote the QA override and started the build, but the Debian package mirror delivered ~1 package per minute and the image had not finished after ~30 minutes, so I stopped it (nothing is left running). Consequently **not verified**: MySQL-specific behaviour (strict-mode length errors; one test is skipped for this reason), **WeasyPrint PDF rendering** (the library's native dependencies are missing on this Windows host, so PDF/email-with-PDF was tested with a stub), the real sidecar container, Celery worker/beat at runtime, and the dev-compose naming bugs (QA-041 is from reading the file). To finish: `docker compose --env-file qa/.env.qa -p filaqa -f docker-compose.yml -f qa/docker-compose.override.yml up -d --build` on a faster connection, then run the QA suite with a MySQL `DATABASE_URL`.
- **Real WhatsApp send/receive.** The brief's test-number placeholder was not filled in, so nothing was sent to anyone; behaviour was proven with stubs plus the three registration-only connections. Media/caption messages, group-admin changes and phone-format variants beyond the JID format are covered by code reading only.
- **Production behaviour.** By your rule I did not connect to it; everything about production comes from the files and the logs you pasted. The unfilled brief fields (known symptoms, extra logs) mean I analysed only the sidecar log and `RestartCount`. Your Django log excerpt shows only internet-scanner 404s and nothing from WhatsApp, consistent with a sidecar that cannot connect.
- **SMTP delivery, GoMotive live API, Gmail limits**: stubbed or read only.
- **Arabic/RTL:** the site has no RTL support (`lang="en"`, no `dir`); Arabic *text* was tested (stores and renders, does not crash). Not treated as a defect since the brief only asks if the site has it.
- **Load/performance under real data volume, accessibility with a screen reader, cross-browser (Chromium only), the Django admin.**

## 6. Notes on method and housekeeping

- I did not read your `.env`; its effect shows in the `check --deploy` result, so QA-029 is flagged *suspected* for production and the diagnostics script prints the real values.
- Files created: everything is under `qa/` (tests, tools, evidence, screenshots, override compose, mock sidecar, scripts) plus one sentence corrected in `CLAUDE.md` (it wrongly said Django honours the secret env var). No application code was changed. `qa/tools/node_modules` and `qa/tools/audit` are scratch installs (git-ignored by `qa/.gitignore`; delete them freely). Docker Desktop was started to test the stack and is still running.
- The three Baileys experiments are the only outside network contacts besides package registries; they used throw-away sessions that were deleted.
- Severity follows your definitions; QA-006 and QA-024 are *High* because I do not know if the repository has ever been public: if it has, both become Critical and the secrets must be rotated first.

## 7. Recommended order once you approve

1. **Same-day:** QA-001 (restore WhatsApp) together with QA-002/003/004 (close the doors, rotate the secret) and QA-007 (shipment detail crash). One deploy, listed steps in Phase 2.
2. **This week:** QA-005, 006, 008–014, 015–020, 021, 022, 026, 027.
3. **Next:** the Medium and Low items, with the QA suite turned into CI.
