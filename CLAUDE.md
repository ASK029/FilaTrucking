# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

FilaTrucking is a single-user Trucking Management System (Django + MySQL) for a solo dispatcher. `AGENTS.md` (UI/Tailwind design tokens, component patterns, nav order, mobile rules) and `docs/PRD.md` (business logic) are the authoritative companions to this file — read them before UI or business-logic work. `docs/tasks.md` is the milestone log of what has been built.

## Commands

All Django commands run from `FilaTrucking/` (the folder containing `manage.py`), with `.venv` activated (`.venv\Scripts\activate` on Windows). MySQL must be reachable: `DATABASE_URL`, `SECRET_KEY` and `WHATSAPP_INGEST_SECRET` have no defaults and settings.py refuses to start without them (copy `.env.example` to `.env`).

```powershell
python manage.py runserver
python manage.py makemigrations ; python manage.py migrate
python manage.py test                                  # all tests
python manage.py test shipments.tests.FinancialTasksTests.test_generate_monthly_statement   # single test
celery -A FilaTrucking worker -l info                  # needs Redis
celery -A FilaTrucking beat -l info                    # schedule lives in settings.CELERY_BEAT_SCHEDULE
python manage.py fetch_whatsapp_groups
python manage.py import_expenses "<csv>" [--clear]     # historical statement CSV import
black . ; isort . ; flake8 .                           # per AGENTS.md; no config files exist
```

Sidecar (Node, from `sidecar/`): `npm install` (runs `patch-package` postinstall — Baileys is patched) then `node index.js`.
Docker: `docker-compose.yml` (dev: mysql, redis, django runserver, sidecar) and `docker-compose.prod.yml` (gunicorn + nginx + certbot; no Redis/Celery service defined). The Dockerfile build context is the repo root and installs `requirements.txt`, which is **UTF-16LE** encoded (`requirements_clean.txt` / `requirements_utf8.txt` are the readable copies).

Note: `.gitignore` contains `.*`, so dotfiles (`.env`, `.vscode`, `.agents`) are untracked by default. `sidecar/node_modules` is committed to git.

## Architecture

Four Django apps under `FilaTrucking/`, but **responsibilities don't match app names**:

- `shipments` is the catch-all: Shipment, Invoice/InvoiceLineItem, Expense, IFTARate, all WhatsApp models (`WhatsAppMessage/Config/Group/Log`), and the `SystemSettings` singleton. It is mounted at **four URL prefixes** from the project `urls.py` via separate URLconfs: `shipment_urls.py` → `/shipments/` (also hosts `api/…` WhatsApp endpoints), `urls.py` → `/invoices/`, `expense_urls.py` → `/expenses/`, `settings_urls.py` → `/settings/` (system settings, connection tests, in-app CSV import).
- `vehicles` holds Vehicle, **IFTAMileage** (per vehicle/state/month/year miles + gallons; the IFTA UI/views live here, not in `shipments`), Maintenance, the GoMotive client and the sync task.
- `customers`, `drivers` are plain CRUD apps.
- The project package `FilaTrucking/FilaTrucking/views.py` holds the dashboard and the monthly/yearly financial statement views (HTML + WeasyPrint PDF) — reporting is function views here, not in an app.

### Data flows spanning several files

- **WhatsApp ingestion:** `sidecar/index.js` (Baileys + Express on :3001) → `POST /shipments/api/ingest/` with `Authorization: Bearer <secret>` → `shipments/api.py:ingest_whatsapp_message` parses the text (key:value format *or* positional booking/container/seal/[customer]/[date]), resolves Customer (abbreviation, then name), Driver (name, then sender phone) and Vehicle (registration or VIN), creates a `pending_review` Shipment, and returns a `reply_message` the sidecar sends back to the group. Duplicate container within the same month sets `is_flagged`. Reverse direction: Django's `trigger_*` endpoints call the sidecar REST API (`WHATSAPP_SIDECAR_URL`), and the sidecar pushes status/QR/groups to `/shipments/api/whatsapp/update-status/` and `sync-groups/`. The shared secret is a hard-coded constant: Django's `api.py` never reads `WHATSAPP_INGEST_SECRET` from the environment (settings.py doesn't define it), the sidecar reads `DJANGO_SECRET` but defaults to the same constant. The sidecar REST API (:3001) has no auth. See `qa/TASKS.md` before touching WhatsApp code.
- **Shipment → invoice → payment:** statuses `pending_review → confirmed → invoiced`; invoice `draft → sent → paid`. Invoices copy shipment fields into `InvoiceLineItem` rows (they are snapshots, not live links; `shipment` FK is nullable). `invoice_pdf`/`invoice_email` render `shipments/invoice_pdf.html` with WeasyPrint (imported in a try/except — `HTML is None` if native libs are missing). Emailing flips status to `sent` and clears `paid_at`.
- **Financial reports count only `paid` invoices**, bucketed by `paid_at` (not `invoice_date`), minus `Expense` rows in the date range. The per-category CSV-style breakdown in `FilaTrucking/views.py` is computed by matching `"Imported: <label>"` in `Expense.notes` (set by the CSV importer), not by the `category` field — manually entered expenses won't appear in those columns. Driver pay comes from `Expense(category=DRIVER)` grouped by driver.
- **Runtime-configurable secrets:** `SystemSettings` (singleton via `get_instance()`) stores the Motive API key and SMTP user/password Fernet-encrypted (`shipments/utils.py`, key derived from `SECRET_KEY` — changing `SECRET_KEY` makes stored secrets undecryptable, and `decrypt_value` silently returns `""`). `vehicles.gomotive_client.get_client()` reads these, falling back to `GOMOTIVE_API_KEY`/`GOMOTIVE_BASE_URL` env vars. Invoice email uses the stored SMTP credentials with host/port from `settings.py` (Gmail).
- **Celery:** `FilaTrucking/celery.py` autodiscovers `tasks.py`; schedule is in `settings.CELERY_BEAT_SCHEDULE` and the dashboard's schedule table renders from it (add a label in `label_map` in the dashboard view for new entries). Tasks: `shipments.tasks` (statements, recurring invoices, IFTA reminders) and `vehicles.tasks.sync_gomotive_data`. Dashboard "Test Report"/"Sync Schedule" buttons call `.delay()` on these.

### Deviations from the PRD (code is the truth)

- IFTA is **monthly** (`IFTAMileage.month`), while `IFTARate` is still keyed by quarter/year; `ifta_report` maps month→quarter. `tax_owed` was intentionally removed. Miles and gallons are entered through separate forms.
- Vehicle identity: VIN (`chassis_number`) is the intended primary identifier in the UI, but the model's `registration_number` is labeled "Truck Number" and is still used for lookups/ordering.
- Expense categories are the CSV-derived set in `ExpenseCategory` (IRP, PARKING, MAINTENANCE, TRUCK, CHECK_CHARGE, INSURANCE, TOLL, FUEL, CHASSIS, DRIVER, OTHER), not the PRD's shorter list.
- `Customer.delete()` raises `ValidationError` if shipments/invoices exist.

### Known rough edges

- `shipments.tasks.generate_monthly_statement` references an undefined `driver_pay_total` and will raise `NameError`; the existing test in `shipments/tests.py` creates an Invoice with `customer_id=None`. Fix these before relying on either.
- Several `api.py` WhatsApp GET endpoints are `@csrf_exempt` with no auth check; `check_auth` (Bearer secret **or** logged-in user) guards only the POST/trigger ones.
- `test_setup.py`, `run_migration.py`, `create_admin.py` and `tmp/` (all inside the Django source root) are throwaway helper scripts, not part of the app.
- Test coverage is minimal (`customers`/`drivers` tests are empty stubs; `vehicles/test_gomotive.py` is plain `unittest` with mocked `requests`).
