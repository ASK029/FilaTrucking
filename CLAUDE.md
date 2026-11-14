# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

FilaTrucking is a single-user Trucking Management System (Django + MySQL) for a solo dispatcher. `AGENTS.md` (UI/Tailwind design tokens, component patterns, nav order, mobile rules) and `docs/PRD.md` (business logic) are the authoritative companions to this file — read them before UI or business-logic work. `docs/tasks.md` is the milestone log of what has been built. `qa/` holds the QA audit (`QA_REPORT.md`, `TASKS.md`, `WHATSAPP_INVESTIGATION.md`, `DEPLOYMENT.md`) and its regression tests; every fix is a commit named `QA-0xx`.

## Commands

All Django commands run from `FilaTrucking/` (the folder containing `manage.py`), with a venv built from `requirements.txt` (`python -m venv .venv && pip install -r requirements.txt`; the file is plain UTF-8 and fully pinned). `DATABASE_URL`, `SECRET_KEY` and `WHATSAPP_INGEST_SECRET` have **no defaults** and settings.py refuses to start without them (copy `.env.example` to `.env`; use the application DB user, never root). `DEBUG` defaults to False.

```powershell
python manage.py runserver
python manage.py makemigrations ; python manage.py migrate
qa/run_tests.sh                                        # (bash, repo root) everything below on SQLite with fake env; QA_DATABASE_URL=mysql://... runs it on MySQL
python manage.py test shipments vehicles customers drivers qa.tests   # same thing by hand; needs PYTHONPATH=<repo root> and the fake env from qa/run_tests.sh
node --test qa/tests/sidecar.test.cjs                  # sidecar behaviour tests (Baileys/Django stubbed; nothing connects to WhatsApp)
celery -A FilaTrucking worker -l info                  # needs Redis
celery -A FilaTrucking beat -l info                    # schedule lives in settings.CELERY_BEAT_SCHEDULE
python manage.py fetch_whatsapp_groups
python manage.py import_expenses "<csv>" [--clear]     # historical statement CSV import (driver pay is stored as category DRIVER)
cd frontend && npm ci && npm run build                 # rebuild FilaTrucking/static/css/app.css after changing templates' Tailwind classes (committed output)
```

Sidecar (Node 24, from `sidecar/`): `npm ci` then `node index.js` (needs `DJANGO_SECRET` = the same value as `WHATSAPP_INGEST_SECRET`; refuses to start without it). `node_modules` is not committed.
Docker: `docker-compose.yml` (dev: mysql, redis, django runserver, sidecar) and `docker-compose.prod.yml` (mysql, redis, django/gunicorn, celery-worker, celery-beat, sidecar, nginx, certbot, backup; only nginx publishes ports; Django runs as `APP_UID:APP_GID`). The Django image is built from the repo root; static files are collected into `./FilaTrucking/staticfiles` by the django container at every start. `deploy/README.md` is the operations runbook (backups, restore, certificates).

Note: `.gitignore` contains `.*`, with explicit exceptions for `.env.example`, `.dockerignore`, `.gitattributes`, `.github/`. Uploaded files (`FilaTrucking/media/`), statements/exports in `docs/` and `gomotive_resp.json` are git-ignored: the repository is **public**.

## Architecture

Four Django apps under `FilaTrucking/`, but **responsibilities don't match app names**:

- `shipments` is the catch-all: Shipment, Invoice/InvoiceLineItem, Expense, IFTARate, FinancialStatement (stored statement snapshots), all WhatsApp models (`WhatsAppMessage/Config/Group/Log`), and the `SystemSettings` singleton. It is mounted at **four URL prefixes** from the project `urls.py` via separate URLconfs: `shipment_urls.py` → `/shipments/` (also hosts `api/…` WhatsApp endpoints), `urls.py` → `/invoices/`, `expense_urls.py` → `/expenses/`, `settings_urls.py` → `/settings/` (system settings, connection tests, in-app CSV import).
- `vehicles` holds Vehicle, **IFTAMileage** (per vehicle/state/month/year miles + gallons; the IFTA UI/views live here, not in `shipments`), Maintenance, the GoMotive client and the sync task.
- `customers`, `drivers` are plain CRUD apps.
- The project package `FilaTrucking/FilaTrucking/views.py` holds the dashboard and the monthly/yearly financial statement views (HTML + WeasyPrint PDF) — reporting is function views here, not in an app. Other project-level modules: `throttle.py` (login lockout), `params.py` (validated query-string parsing → 400), `uploads.py` (random upload names + validators), `mixins.py` (`ProtectedDeleteMixin`), `storage.py` (hashed static files).

### Data flows spanning several files

- **WhatsApp ingestion:** `sidecar/index.js` (Baileys + Express on :3001) → `POST /shipments/api/ingest/` with `Authorization: Bearer <shared secret>` → `shipments/api.py:ingest_whatsapp_message`. It ignores chit-chat (`looks_like_shipment_message`), de-duplicates on the WhatsApp message id (`WhatsAppMessage.dedupe_key`), parses the text (key:value format *or* positional booking/container/seal/[customer]/[date]), resolves Customer (abbreviation, then name), Driver (named, else exact E.164 match of the sender's phone) and Vehicle, takes the rate from the message or `customer.default_rate`, creates a `pending_review` Shipment and returns a `reply_message` (only for success/flagged) that the sidecar posts to the group. Reverse direction: Django's `trigger_*` endpoints call the sidecar REST API (`WHATSAPP_SIDECAR_URL`, bearer secret); the sidecar pushes status/QR/groups to `/shipments/api/whatsapp/update-status/` and `sync-groups/`. The secret comes only from the environment on both sides; the sidecar API rejects every call without it except `GET /health`. The sidecar bot processes only groups the admin enabled (new groups are disabled by default; the filter fails closed), keeps unsent messages in `auth_data/_outbox.json` and retries them, and reconnects with backoff (it fetches the current WhatsApp-Web version at every connect: never pin it). See `qa/WHATSAPP_INVESTIGATION.md`.
- **Shipment → invoice → payment:** statuses `pending_review → confirmed → invoiced`; invoice `draft → sent → paid`. Invoices copy shipment fields into `InvoiceLineItem` rows (snapshots; `shipment` FK is nullable); a shipment can be on only one invoice, and removing a line restores it to `confirmed`. `invoice_pdf`/`invoice_email` render `shipments/invoice_pdf.html` with WeasyPrint (imported in a try/except — `HTML is None` if native libs are missing). Emailing is POST-only and only moves `draft → sent` (it never touches `paid_at`).
- **Financial reports count only `paid` invoices**, bucketed by `paid_at` (not `invoice_date`), minus `Expense` rows in the date range. The CSV-style columns group by `Expense.category`; driver pay is `Expense(category=DRIVER)` grouped by driver. The Celery tasks `generate_monthly/yearly_statement` store a `FinancialStatement` row per period (beat on the 1st reports the period that just ended).
- **Runtime-configurable secrets:** `SystemSettings` (singleton via `get_instance()`) stores the Motive API key and SMTP user/password Fernet-encrypted (`shipments/utils.py`, key derived from `SECRET_KEY` — changing `SECRET_KEY` makes stored secrets undecryptable, and `decrypt_value` silently returns `""`). The settings page never renders them back (empty field = keep). `vehicles.gomotive_client.get_client()` reads these, falling back to `GOMOTIVE_API_KEY`/`GOMOTIVE_BASE_URL` env vars.
- **Celery:** `FilaTrucking/celery.py` autodiscovers `tasks.py`; schedule is in `settings.CELERY_BEAT_SCHEDULE` (`recurring-invoices` is opt-in via `ENABLE_RECURRING_INVOICES=True`) and the dashboard's schedule table renders from it (add a label in `label_map` in the dashboard view for new entries). Tasks: `shipments.tasks` (statements, recurring invoices, IFTA reminders, `prune_whatsapp_data`) and `vehicles.tasks.sync_gomotive_data`. Dashboard buttons queue with a short retry policy and show an error message if the broker is down.
- **Time zone:** `America/Chicago` (settings `TIME_ZONE`); use `timezone.localdate()`, never `date.today()`.
- **Uploads:** stored under random names; served only through the login-protected `/media/<path>` view (nginx has no `/media/` location).

### Deviations from the PRD (code is the truth)

- IFTA is **monthly** (`IFTAMileage.month`), while `IFTARate` is still keyed by quarter/year; `ifta_report` maps month→quarter. `tax_owed` was intentionally removed. Miles and gallons are entered through separate forms.
- Vehicle identity: VIN (`chassis_number`) is the intended primary identifier in the UI, but the model's `registration_number` is labeled "Truck Number" and is still used for lookups/ordering. Vehicle status codes are `AV/IU/MA/RE` (Available, In Use, Maintenance, Retired).
- Expense categories are the CSV-derived set in `ExpenseCategory` (IRP, PARKING, MAINTENANCE, TRUCK, CHECK_CHARGE, INSURANCE, TOLL, FUEL, CHASSIS, DRIVER, OTHER), not the PRD's shorter list.
- Customer/Driver/Vehicle foreign keys on shipments, invoices, expenses, IFTA and maintenance are `PROTECT`: trucks and drivers with history are retired/deactivated, not deleted.
- The PRD's per-customer recurring-invoice schedule is not implemented (QA-042).

### Known rough edges

- `test_setup.py`, `run_migration.py`, `create_admin.py` and `tmp/` (all inside the Django source root) are throwaway helper scripts, not part of the app.
- WeasyPrint PDF rendering cannot be exercised on Windows (native libs missing): verify PDFs in the Docker image.
- Maintenance rows are ordered in Python (`status_group` is computed), after `select_related("vehicle")`.
- Google Fonts is still loaded from Google; Tailwind classes are compiled into the committed `static/css/app.css` (rebuild it when you add new classes).
- Not yet done (see `qa/TASKS.md`): MySQL 8.0 → 8.4 upgrade, 2FA for the admin, off-server backup copy, history purge of the public repository.
