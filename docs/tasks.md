# Project Tasks

## Milestone 1: Project Setup & Core Schema
- [X] Initialize Django 6.0.1+ project and application structure (apps: `customers`, `drivers`, `shipments`, `vehicles`).
- [X] Create Python virtual environment and `requirements.txt`.
- [X] Set up MySQL database, configure `settings.py`.
- [X] Extend existing database schema for Drivers, Customers, Vehicles, Shipments, Expenses, IFTA.
- [X] Run `makemigrations` and `migrate`.
- [X] Build `base.html` with sidebar nav (following order in AGENTS.md §7.6), top bar, mobile drawer.
- [X] Add Tailwind CDN, design tokens (brand, accent, surface, muted), and fonts (DM Sans, JetBrains Mono) to `base.html` (§7.2).
- [X] Implement Django flash messages block with styling for success, error, warning, info (§7.9).

## Milestone 2: Core Management Modules
- [X] Implement CRUD ModelForms and Class-Based Views (CBVs) for Drivers.
- [X] Implement CRUD ModelForms and CBVs for Customers.
- [X] Implement CRUD ModelForms and CBVs for Vehicles.
- [X] Create HTML templates for list and form views for the above modules.
- [X] Ensure all list views include search/filter functionality (collapsing behind a "Filters" toggle button on mobile).
- [X] Add status badges to list views using inline pill spans matching AGENTS.md §7.4.
- [X] Implement empty state blocks with Heroicons for all list views (§7.8).
- [X] Ensure list views are mobile-responsive (overflow-x-auto, sticky first column, tap targets ≥ 44px) per §7.4 and §7.5.

## Milestone 3: WhatsApp Ingestion & Shipment Management
- [X] Develop Node.js (Baileys) sidecar script for WhatsApp message parsing.
- [X] Build Django REST API endpoint with secret key auth for payload ingestion from Node.js sidecar.
- [X] Implement Shipment model status choices (`pending_review`, `confirmed`, `invoiced`).
- [X] Build Shipment list UI, incorporating specific status badge pill classes (`pending_review` → `confirmed` → `invoiced`) per §7.4.
- [X] Build Shipment detail and review UI for confirming ingested shipments.
- [X] Allow admin to edit auto-ingested fields and manually create/edit Shipments (Fallback).
- [X] Add validation to flag missing required fields or duplicate containers within billing period.

## Milestone 4: Invoicing & Expense Tracking
- [X] Implement `Expense` model and categories (Driver Pay, Repairs/Maintenance Parts, Insurance, Fuel, Tolls, Other).
- [X] Build Expense Tracking module CRUD views.
- [X] Build Expenses list view following table and card patterns from §7.4.
- [X] Implement one-time management command or view for CSV bulk import and automatic column mapping.
- [X] Implement `Invoice` models grouping `confirmed` shipments by customer + date range.
- [X] Implement WeasyPrint PDF invoice generation matching the `invoice_91.pdf` reference layout.
- [X] Implement email delivery of PDFs using Django SMTP backend and provide a downloadable copy.
- [X] Apply list view table and card patterns to Invoices.

## Milestone 5: Financials & IFTA Calculation
- [ ] Implement `IFTAMileage` entry model (State, Vehicle, Quarter, Year, Miles).
- [ ] Implement `IFTARate` model (State, Quarter, Year, Rate).
- [ ] Implement IFTA calculation logic (calculating fuel tax owed or computed gallons) and quarterly reporting UI.
- [ ] Develop Monthly Financial Statement generation logic (Gross Revenue, Expenses by category, Net Profit, Driver Pay breakdown).
- [ ] Develop Yearly Financial Statement aggregated logic.
- [ ] Build Financial pages using stat card components (§7.4).
- [ ] Ensure financial reports are exportable to PDF without breaking the layout.

## Milestone 6: Automation, Integrations & Dashboard
- [ ] Integrate Celery and Redis into Django settings for task scheduling.
- [ ] Create Celery tasks for scheduled reports (Monthly statement, Yearly statement, Recurring invoices) and IFTA reminders.
- [ ] Integrate GoMotive REST API (syncing odometer readings and maintenance alerts).
- [ ] Setup scheduled Celery beat task for nightly GoMotive mileage/maintenance sync.
- [ ] Build central Dashboard UI.
- [ ] Add Dashboard stat cards: Revenue, active shipments, pending invoices, maintenance alerts (§7.4).
- [ ] Add Schedule overview table on Dashboard for report schedules.
- [ ] Trigger dashboard alerts when a GoMotive or manual vehicle is within 500 miles of next service.

## Milestone 7: Data Migration & Final Polish
- [ ] Create management command to migrate `2025_STATMENTS_-_JAN.csv` (Expenses + Deposits).
- [ ] Create management command to migrate `IFTA2025_2ND.xlsx` (IFTA historical records).
- [ ] Conduct end-to-end testing of the complete dispatcher workflow (WhatsApp message → confirmed shipment → invoice PDF sent).
- [ ] UI audit: verify every page passes full mobile access (390px wide screen check).
- [ ] UI audit: verify empty states exist on every list view.
- [ ] UI audit: verify all status badges strictly use their correct predefined pill classes.
- [ ] UI audit: verify all clickable targets are ≥ 44×44 px.