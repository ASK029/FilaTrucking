# Fila Trucking Management System — PRD

## Summary

A web-based TMS built in Django for a solo dispatcher managing a small fleet. Shipments enter via a WhatsApp group (drivers type structured messages parsed by a bot). Admin manages drivers, customers, vehicles, and shipments via CRUD. System handles invoicing (PDF by email, batched per customer per period), expense tracking, monthly/yearly financial statements, IFTA calculation (manual mileage entry, computed gallons), and vehicle maintenance tracking. Three trucks integrate with GoMotive API for auto mileage and maintenance; remaining vehicles are manual. All reporting is schedulable.

---

## Problem

Operations are fully manual: shipments tracked on paper/WhatsApp, statements in Excel, invoices typed per customer, IFTA calculated manually, maintenance untracked. No single source of truth. Admin time is wasted on data entry and reconciliation rather than operations.

---

## Users

- **Admin/Dispatcher (solo):** Only user. Full access to everything.

---

## Core Modules

### 1. WhatsApp Ingestion

**How it works:**
- Baileys (whatsapp-web.js) runs as a Node.js sidecar service alongside Django.
- Listens to a designated WhatsApp group.
- Drivers send messages in a strict format (defined below).
- Sidecar POSTs parsed payload to a Django internal API endpoint.
- Django creates a `Shipment` record with status `pending_review`.
- Admin reviews, edits if needed, and confirms.

**Message format (driver-typed):**
```
DATE: 01/05/26
BOOKING: RICG02390500
CONTAINER: NYKU5132858
SEAL: 101039
CUSTOMER: ASL
RATE: 400
DRIVER: [name or ID]
TRUCK: [plate or ID]
```

**Parser rules:**
- Fields are key-value, colon-separated, one per line.
- Case-insensitive keys.
- Missing required fields → message flagged, not auto-created. Admin gets alert in dashboard.
- Duplicate container number within same billing period → warning flag.

**Fallback:** Admin can manually create shipments via web form (same fields).

---

### 2. Shipment Management

**Shipment record fields:**
- Date, Booking #, Container #, Seal #, Customer, Driver, Vehicle, Rate, Status, Notes

**Statuses:** `pending_review` → `confirmed` → `invoiced`

**CRUD:** Admin can create, edit, delete, filter by customer / date range / status / driver / vehicle.

**Bulk actions:** Select multiple shipments → assign to invoice.

---

### 3. Driver Management

- Fields: Name, Phone, License #, License Expiry, Status (active/inactive)
- CRUD via admin UI
- Filter by status
- Driver linked to shipments

---

### 4. Customer Management

- Fields: Name, Email (for invoice delivery), Phone, Address, Notes
- CRUD via admin UI
- Each customer has a default rate (overridable per shipment)

---

### 5. Vehicle Management

**Two modes per vehicle:**

| Mode | Trigger | Mileage Source | Maintenance Source |
|---|---|---|---|
| GoMotive | Vehicle has GoMotive ID | Auto via API | Auto via API |
| Manual | No GoMotive ID | Admin enters manually | Admin enters manually |

**Vehicle fields:** Plate, Make, Model, Year, GoMotive ID (optional), MPG (miles per gallon), Status (active/inactive)

**GoMotive sync:**
- Runs on a configurable schedule (e.g., nightly).
- Pulls: odometer readings, maintenance alerts/events.
- Manual override always available even for GoMotive vehicles.

**Maintenance tracking:**
- Log entries: Date, Vehicle, Type (oil change / tire / brake / other), Description, Cost, Mileage at service, Next service mileage (optional)
- Dashboard alert when vehicle is within 500 miles of next service mileage.

---

### 6. Invoicing

**Generation:**
- Admin selects customer + date range → system lists all `confirmed` shipments for that customer in range.
- Admin reviews line items, adjusts if needed.
- Invoice format matches current format (invoice #91 style): header with company info, table of DATE / BOOKING / CONTAINER / SEAL / LOCATION / AMOUNT, total.
- Invoice number auto-incremented.
- PDF generated server-side (WeasyPrint or ReportLab).

**Delivery:**
- PDF attached to email sent to customer's email on file.
- Email sent from within the web app.
- Copy of PDF stored in system, downloadable anytime.

**Invoice statuses:** `draft` → `sent` → `paid`

**Scheduling:** Admin can set a recurring invoice schedule per customer (e.g., generate every 15th of the month for ASL). System generates draft automatically — admin reviews before sending.

---

### 7. Expense Tracking

**Expense categories:**
- Driver Pay
- Repairs / Maintenance Parts
- Insurance
- Fuel *(tracked for IFTA, separate sub-module)*
- Tolls
- Other

**Expense record fields:** Date, Category, Amount, Vehicle (optional), Driver (optional), Notes, Receipt upload (optional)

**Entry:** Manual via web form. Bulk CSV import supported.

**Data observed from statements CSV:**
- Existing columns map to: IRP → Other, Parking → Other, On Site → Other, Truck (purchase) → Other, Check Charge → Other, Insurance → Insurance, Toll → Tolls, Fuel → Fuel, Chassis → Other, Pay + Pay To → Driver Pay.
- On import, system maps columns to categories automatically with admin confirmation step.

---

### 8. IFTA Calculation

**Scope:** Illinois + occasional bordering states. Simplified UX — no per-state complexity exposed unless needed.

**Per vehicle per quarter:**
- Admin enters: miles driven per state (IL, IN, WI, MO, KY — pre-populated list)
- Admin enters: total gallons purchased (or system calculates: total miles ÷ vehicle MPG)
- System calculates: fuel tax owed per jurisdiction using stored IFTA rates (admin updates rates quarterly)

**IFTA record fields:** Vehicle, Quarter, Year, State, Miles Driven, Gallons (computed or manual override), Tax Rate, Tax Due

**Output:** Quarterly IFTA summary report, exportable as PDF.

**Scheduling:** System reminds admin 2 weeks before IFTA quarterly deadline (Jan 31, Apr 30, Jul 31, Oct 31).

---

### 9. Financial Statements

**Monthly Statement:**
- Gross Revenue (sum of invoiced shipments)
- Expenses by category
- Net Profit
- Driver pay breakdown by driver (matches current CSV: MOE, TAZ, MOHAB columns)
- Exportable as PDF

**Yearly Statement:**
- Aggregated from monthly statements
- Same structure, used for tax filing

**Scheduling:** Auto-generate monthly statement on 1st of following month as draft. Admin reviews and locks it.

---

### 10. Reporting & Scheduling Dashboard

Central place to manage all scheduled tasks:

| Report | Schedule | Action |
|---|---|---|
| Invoice per customer | Configurable per customer | Auto-draft, admin sends |
| Monthly statement | 1st of each month | Auto-draft |
| Yearly statement | Jan 1 | Auto-draft |
| IFTA reminder | 2 weeks before deadline | Notification |
| GoMotive sync | Nightly | Auto |

Admin can manually trigger any report at any time.

---

## Technical Constraints

- **Stack:** Django + Django Templates (existing)
- **PDF generation:** WeasyPrint (CSS-based, easier to style to match existing invoice format)
- **Email:** Django email backend (SMTP configurable — SendGrid or Gmail)
- **WhatsApp sidecar:** Node.js service using Baileys, communicates with Django via internal REST endpoint with shared secret key
- **GoMotive:** REST API integration, credentials stored in environment variables, sync via Django management command + cron/Celery beat
- **Database:** MySQL
- **Task scheduling:** Celery + Redis (for scheduled reports, GoMotive sync, email delivery)
- **Existing ERD:** Respect existing schema. Extend, don't replace.

---

## Existing Data Migration

Based on uploaded files:

| Source | Target Module |
|---|---|
| `2025_STATMENTS_-_JAN.csv` | Expenses + Deposits (historical import) |
| `invoice_91.pdf` | Invoice format reference (replicate exactly) |
| `IFTA2025_2ND.xlsx` | IFTA historical records (import tool) |
| `FilaTruckingERD.drawio` | Base schema (extend only) |

Migration tooling: one-time Django management commands per source file, with dry-run mode.

---

## Out of Scope (v1)

- Driver-facing mobile app or portal
- Multi-user / role-based access
- Accounting software integration (QuickBooks etc.)
- Real-time GPS tracking beyond GoMotive API
- Automated per-state mileage detection (IFTA remains manual entry)
- Customer portal for invoice viewing

---

## Success Criteria

- Admin can go from WhatsApp message → confirmed shipment → invoice PDF sent to customer without touching any tool outside this system
- Monthly financial statement generated in under 30 seconds
- IFTA quarterly report generated with only mileage input required from admin
- Zero manual Excel/CSV work for routine operations
