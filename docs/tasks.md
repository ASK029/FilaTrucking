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
- [X] Implement `IFTAMileage` entry model (State, Vehicle, Quarter, Year, Miles).
- [X] Implement `IFTARate` model (State, Quarter, Year, Rate).
- [X] Implement IFTA calculation logic (calculating fuel tax owed or computed gallons) and quarterly reporting UI.
- [X] Develop Monthly Financial Statement generation logic (Gross Revenue, Expenses by category, Net Profit, Driver Pay breakdown).
- [X] Develop Yearly Financial Statement aggregated logic.
- [X] Build Financial pages using stat card components (§7.4).
- [X] Ensure financial reports are exportable to PDF without breaking the layout.

## Milestone 6: Automation, Integrations & Dashboard
- [X] Integrate Celery and Redis into Django settings for task scheduling.
- [X] Create Celery tasks for scheduled reports (Monthly statement, Yearly statement, Recurring invoices) and IFTA reminders.
- [X] Integrate GoMotive REST API (syncing odometer readings and maintenance alerts).
- [X] Setup scheduled Celery beat task for nightly GoMotive mileage/maintenance sync.
- [X] Build central Dashboard UI.
- [X] Add Dashboard stat cards: Revenue, active shipments, pending invoices, maintenance alerts (§7.4).
- [X] Add Schedule overview table on Dashboard for report schedules.
- [X] Trigger dashboard alerts when a GoMotive or manual vehicle is within 500 miles of next service.

## Milestone 7: Data Migration & Final Polish
- [X] Create management command to migrate `2025_STATMENTS_-_JAN.csv` (Expenses + Deposits).
- [X] Create management command to migrate `IFTA2025_2ND.xlsx` (IFTA historical records).
- [X] Conduct end-to-end testing of the complete dispatcher workflow (WhatsApp message → confirmed shipment → invoice PDF sent).
- [X] UI audit: verify every page passes full mobile access (390px wide screen check).
- [X] UI audit: verify empty states exist on every list view.
- [X] UI audit: verify all status badges strictly use their correct predefined pill classes.
- [X] UI audit: verify all clickable targets are ≥ 44×44 px.

## Milestone 8: Module Refinements & Workflow Improvements

### Dashboard
- [X] Add manual "Test Report" button to trigger report generation on demand.
- [X] Add manual "Sync Schedule" button to trigger GoMotive/schedule sync on demand.

### Customers
- [X] Rename `street` field to `address` in model, forms, and templates.
- [X] Rename `address_1` field to `city_state` (City & State) in model, forms, and templates.
- [X] Replace `address_2` field with `country` field, default value `United States`, and remove it from the form (auto-populated).
- [X] Remove the "Delete" button from the Customer detail/form view.
- [X] Add validation to prevent deleting a Customer that has child records (shipments, invoices, etc.).
- [X] Remove the `status` column from the Customer list view.
- [X] Remove `/ mile` display from the default rate field in the Customer list/detail views.
- [X] Fix Customer list search functionality (not returning results).
- [ ] Fix Customer list filter functionality (filters not applying correctly).

### Drivers
- [X] Add `joined` (date joined) field to the Driver model.
- [X] Display `joined` date in the Driver list view.
- [X] Add `joined` date input to the Driver create/edit form.
- [X] Make `license_expiry` field not required in the Driver model and form.

### Vehicles
- [X] Make `driver` field not required in the Vehicle model and form.
- [X] Make `manufacturer` field not required in the Vehicle model and form.
- [X] Make `model_year` field not required in the Vehicle model and form.
- [X] Make `vehicle_image` field not required and add a placeholder image when empty.
- [X] Remove `engine_number` field from the Vehicle model, form, and templates.
- [X] Remove `name` field from the Vehicle model, form, and templates.
- [X] Use VIN (Chassis Number) as the primary vehicle identifier across all views and references.

### IFTA
- [X] Move IFTA module under the "Reports" navigation option (sub-menu or nested route).
- [X] Set default state to `Illinois` in the IFTA entry form.
- [X] Change IFTA reporting granularity from quarterly to monthly.
- [X] Create a separate form for entering miles driven per state/vehicle/month.
- [X] Create a separate form for entering gallons consumed per state/vehicle/month.
- [X] Display Vehicle VIN (instead of name/plate) as the vehicle identifier in IFTA reports.
- [ ] Remove `tax_owed` field/column from IFTA reports and calculations.

### Maintenance
- [ ] Make `mileage_at_service` field not required in the Maintenance model and form.
- [ ] Make `next_service_mileage` field not required in the Maintenance model and form.
- [ ] Make `next_service_due` field not required in the Maintenance model and form.

### Shipments
- [ ] Make `vehicle` field not required in the Shipment model and form.
- [ ] Make `driver` field not required in the Shipment model and form.
- [ ] Make `rate` field not required; auto-populate from Customer's default rate when available.

### Invoices
- [ ] Auto-populate invoice line items from shipments matching the selected date range (from/to).
- [ ] Pre-fill invoice line item fields from corresponding shipment data.
- [ ] Allow removing individual line items that are not needed before saving.
- [ ] Add a separate "Change Status" action (independent from the edit form).
- [ ] Add `paid_at` date field to the Invoice model; display in reports when set.
- [ ] **PDF Redesign:** Make each invoice field a separate column in the table.
- [ ] **PDF Redesign:** Reorder columns to: Date, Booking, Container, Seal, Location, Description, Amount.
- [ ] **PDF Redesign:** Remove tax row/column from the invoice PDF.
- [ ] **PDF Redesign:** Add customer info block (address, phone number, email) to the PDF header.
- [ ] **PDF Redesign:** Remove notes section from the PDF.
- [ ] **PDF Redesign:** Adjust layout to fill the full page width.

### Expenses
- [ ] Fix expense reports to correctly assign categories (not only "Driver Pay").
- [ ] Update expense categories to: IRP, PARKING, Maintenance, TRUCK, CHECK CHARGE, INSURANCE, TOLL, FUEL, OTHER, CHASSIS.

### Reports
- [ ] Build an in-app UI for migrating/importing data from external CSV files (user-facing, not management command).