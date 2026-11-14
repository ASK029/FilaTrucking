# FilaTrucking – Architecture as tested (Phase 0)

Everything below was read from the repo; items marked *(not verified live)* depend on the production server, which was not touched.

## 1. Components

| Component | Tech | Where | Notes |
|---|---|---|---|
| Web app | Django 6.0.1, class-based + function views, Django templates, Tailwind **Play CDN**, Alpine.js (CDN, floating `3.x.x`) | `FilaTrucking/` | Apps: `customers`, `drivers`, `shipments` (catch-all: shipments, invoices, expenses, WhatsApp models, SystemSettings), `vehicles` (vehicles, IFTA, maintenance, GoMotive). Statements/dashboard are views in the project package. |
| DB | MySQL 8.0 (prod), configured via `DATABASE_URL` (`dj_database_url`) | compose `mysql` | Default URL with a root credential is hard-coded in `settings.py:107`. |
| WhatsApp sidecar | Node 20 + Baileys **6.7.21** (+ Express REST on :3001) | `sidecar/` | Not whatsapp-web.js and not the Cloud API: unofficial WhatsApp-Web protocol client. Session files in `process.cwd()/auth_data`. |
| Reverse proxy / TLS | nginx + certbot loop | `nginx.conf`, `docker-compose.prod.yml` | Serves `/static/`, `/media/` (no auth), proxies `/` to Django and `/sidecar/` to the sidecar. |
| Async jobs | Celery + Redis + beat schedule in `settings.py` | `FilaTrucking/celery.py`, `*/tasks.py` | **Redis, worker and beat are not defined in `docker-compose.prod.yml`.** |
| PDF / email | WeasyPrint (server-side), Django SMTP (credentials from `SystemSettings`, Fernet-encrypted with a key derived from `SECRET_KEY`) | `shipments/views.py` | Synchronous inside the request. |
| GoMotive | REST client | `vehicles/gomotive_client.py`, `vehicles/tasks.py` | Nightly via Celery beat (which is not running in prod). |

### Production topology (docker-compose.prod.yml)

```
Internet ──80/443──> nginx ──/──────────> django:8000 (gunicorn, 4 sync workers)
   │                   └──/sidecar/────> sidecar:3001   (no auth)  <── also published 0.0.0.0:3001
   └──8888───────────────────────────────> django:8000   (bypasses nginx, plain HTTP)
django ──> mysql:3306 (internal)           sidecar ──> django:8000/shipments/api/...
certbot (renew loop, no nginx reload hook)   volumes: mysql_data, auth_data (WhatsApp session), staticfiles, certbot_*
```

## 2. How Django and the sidecar talk

| Direction | Call | Auth | Purpose |
|---|---|---|---|
| sidecar → Django | `POST /shipments/api/ingest/` | `Authorization: Bearer <shared secret>` | every group text message |
| sidecar → Django | `POST /shipments/api/whatsapp/update-status/`, `sync-groups/` | same | status, QR string, group list |
| sidecar → Django | `GET /shipments/api/whatsapp/groups/` (every 30 s) | none required (Django does not check) | which groups are active |
| Django → sidecar | `GET /api/groups`, `POST /api/restart`, `DELETE /api/auth` via `requests` (10 s timeout) | Django sends a Bearer header, **sidecar ignores it** | "Sync", "Restart", "Clear auth" buttons |
| browser → Django | polls `GET /shipments/api/whatsapp/status/` every few seconds on the settings page | none | live status/QR |

The shared secret is the constant in `shipments/api.py:19` / `sidecar/index.js:13`; Django never reads `WHATSAPP_INGEST_SECRET` from the environment (see QA-004).

## 3. Main user flows

1. **WhatsApp → shipment:** driver posts text in an *active* group → Baileys `messages.upsert` → sidecar POSTs to Django → parser (key:value or positional booking/container/seal/customer/date) → `Shipment(status=pending_review)` → bot replies in the group → admin reviews on `/shipments/`, edits, sets `confirmed`.
2. **Shipment → invoice:** `/invoices/create/` (customer + date range pre-fills confirmed shipments) → line items → shipments become `invoiced` → PDF (`/invoices/<id>/pdf/`) → email (`/invoices/<id>/email/`) → status `sent` → manual "Change status" to `paid` (sets `paid_at`).
3. **Money reports:** monthly/yearly statements count only **paid** invoices by `paid_at`, minus expenses; IFTA monthly miles + gallons entry, report with quarterly rates.
4. **Fleet:** vehicles, maintenance (alerts within 500 miles), GoMotive sync (manual button or nightly task).
5. **Admin:** system settings (Motive key, SMTP credentials), CSV expense import, WhatsApp pairing page (QR, groups, logs).

## 4. Pages and endpoints under test (77 routes, generated from the URL resolver)

Single login (`django.contrib.auth`), single admin user, no roles. All routes were hit anonymously (must redirect/401) and logged-in (must not 500) by `qa/tests/test_access_pages_security.py`; 43 HTML pages were rendered in a real browser at 375/768/1440 px (`qa/screenshots/`).

| Path | Name | View |
|---|---|---|
| `/auth/login/` | login | LoginView |
| `/auth/logout/` | logout | LogoutView |
| `/auth/password_change/` | password_change | PasswordChangeView |
| `/auth/password_change/done/` | password_change_done | PasswordChangeDoneView |
| `/auth/password_reset/` | password_reset | PasswordResetView |
| `/auth/password_reset/done/` | password_reset_done | PasswordResetDoneView |
| `/auth/reset/<uidb64>/<token>/` | password_reset_confirm | PasswordResetConfirmView |
| `/auth/reset/done/` | password_reset_complete | PasswordResetCompleteView |
| `/` | dashboard | dashboard |
| `/test-report/` | test_report | test_report |
| `/test-sync-schedule/` | test_sync_schedule | test_sync_schedule |
| `/customers/` | customer_list | CustomerListView |
| `/customers/create/` | customer_create | CustomerCreateView |
| `/customers/<int:pk>/` | customer_detail | CustomerDetailView |
| `/customers/<int:pk>/edit/` | customer_update | CustomerUpdateView |
| `/drivers/` | driver_list | DriverListView |
| `/drivers/create/` | driver_create | DriverCreateView |
| `/drivers/<int:pk>/` | driver_detail | DriverDetailView |
| `/drivers/<int:pk>/edit/` | driver_update | DriverUpdateView |
| `/drivers/<int:pk>/delete/` | driver_delete | DriverDeleteView |
| `/drivers/documents/<int:pk>/delete/` | driver_document_delete | DriverDocumentDeleteView |
| `/shipments/` | shipment_list | ShipmentListView |
| `/shipments/create/` | shipment_create | ShipmentCreateView |
| `/shipments/<int:pk>/` | shipment_detail | ShipmentDetailView |
| `/shipments/<int:pk>/edit/` | shipment_update | ShipmentUpdateView |
| `/shipments/<int:pk>/delete/` | shipment_delete | ShipmentDeleteView |
| `/shipments/api/ingest/` | api_whatsapp_ingest | ingest_whatsapp_message |
| `/shipments/api/whatsapp/status/` | api_whatsapp_status | whatsapp_status |
| `/shipments/api/whatsapp/qr-code/` | api_whatsapp_qr_code | whatsapp_qr_code |
| `/shipments/api/whatsapp/logs/` | api_whatsapp_logs | whatsapp_logs |
| `/shipments/api/whatsapp/groups/` | api_whatsapp_groups | whatsapp_groups |
| `/shipments/api/whatsapp/groups/<int:group_id>/status/` | api_whatsapp_update_group_status | whatsapp_update_group_status |
| `/shipments/api/whatsapp/sync-groups/` | api_whatsapp_sync_groups | whatsapp_sync_groups |
| `/shipments/api/whatsapp/update-status/` | api_whatsapp_update_status | whatsapp_update_status |
| `/shipments/api/whatsapp/trigger-sync/` | api_trigger_sync_groups | trigger_sync_groups |
| `/shipments/api/whatsapp/trigger-restart/` | api_trigger_restart | trigger_restart_connection |
| `/shipments/api/whatsapp/trigger-clear-auth/` | api_trigger_clear_auth | trigger_clear_auth |
| `/shipments/settings/whatsapp/` | whatsapp_settings | WhatsAppSettingsView |
| `/vehicles/` | vehicle_list | VehicleListView |
| `/vehicles/create/` | vehicle_create | VehicleCreateView |
| `/vehicles/<int:pk>/` | vehicle_detail | VehicleDetailView |
| `/vehicles/<int:pk>/edit/` | vehicle_update | VehicleUpdateView |
| `/vehicles/<int:pk>/delete/` | vehicle_delete | VehicleDeleteView |
| `/vehicles/<int:pk>/sync/` | sync_vehicle | sync_vehicle_gomotive |
| `/vehicles/gomotive/` | gomotive_vehicles | gomotive_vehicles_view |
| `/vehicles/ifta/log/miles/` | ifta_log_miles_create | IFTAMilesCreateView |
| `/vehicles/ifta/log/gallons/` | ifta_log_gallons_create | IFTAGallonsCreateView |
| `/vehicles/ifta/logs/` | ifta_log_list | IFTALogListView |
| `/vehicles/ifta/report/` | ifta_report | ifta_report |
| `/vehicles/maintenance/` | maintenance_list | MaintenanceListView |
| `/vehicles/maintenance/create/` | maintenance_create | MaintenanceCreateView |
| `/vehicles/maintenance/<int:pk>/edit/` | maintenance_update | MaintenanceUpdateView |
| `/vehicles/maintenance/<int:pk>/delete/` | maintenance_delete | MaintenanceDeleteView |
| `/invoices/` | invoice_list | InvoiceListView |
| `/invoices/create/` | invoice_create | invoice_create |
| `/invoices/<int:invoice_pk>/lines/` | invoice_add_lines | invoice_add_lines |
| `/invoices/<int:pk>/` | invoice_detail | InvoiceDetailView |
| `/invoices/<int:pk>/change-status/` | invoice_change_status | invoice_change_status |
| `/invoices/<int:pk>/delete/` | invoice_delete | invoice_delete |
| `/invoices/<int:pk>/pdf/` | invoice_pdf | invoice_pdf |
| `/invoices/<int:pk>/email/` | invoice_email | invoice_email |
| `/invoices/<int:invoice_pk>/line-item/add/` | invoice_line_item_add | InvoiceLineItemCreateView |
| `/expenses/` | expense_list | ExpenseListView |
| `/expenses/create/` | expense_create | ExpenseCreateView |
| `/expenses/<int:pk>/edit/` | expense_update | ExpenseUpdateView |
| `/expenses/<int:pk>/delete/` | expense_delete | ExpenseDeleteView |
| `/settings/system/` | system_settings | SystemSettingsView |
| `/settings/test/motive/` | test_motive_connection | TestMotiveConnectionView |
| `/settings/test/email/` | test_email_connection | TestEmailConnectionView |
| `/settings/import/` | csv_import | CSVImportView |
| `/settings/import/process/` | csv_import_process | CSVImportProcessView |
| `/reports/financial/monthly/` | monthly_statement | monthly_statement |
| `/reports/financial/yearly/` | yearly_statement | yearly_statement |
| `/reports/financial/monthly/pdf/` | monthly_statement_pdf | monthly_statement_pdf |
| `/reports/financial/yearly/pdf/` | yearly_statement_pdf | yearly_statement_pdf |
| `/^media/(?P<path>.*)$` | None | serve |
| `/^static/(?P<path>.*)$` | None | serve |

Admin site (`/admin/`) exists and was not tested beyond its login redirect.

## 5. Files that matter most

`sidecar/index.js` (all WhatsApp logic, 502 lines) · `FilaTrucking/shipments/api.py` (ingest parser + control endpoints) · `FilaTrucking/shipments/views.py` (invoices, email, CSV import, settings) · `FilaTrucking/shipments/tasks.py`, `vehicles/tasks.py` (Celery) · `FilaTrucking/FilaTrucking/views.py` (dashboard, statements) · `docker-compose.prod.yml`, `nginx.conf`, `Dockerfile`, `sidecar/Dockerfile`.
