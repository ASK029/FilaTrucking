# AGENTS.md - FilaTrucking Repository Guide

This document provides essential instructions, guidelines, and commands for AI agents operating in the FilaTrucking repository.

## 1. Project Architecture & Environment
FilaTrucking is a specialized Trucking Management System (TMS) built with Django for solo dispatchers.

- **Stack:** Django 6.0.1+ (Python), MySQL, Redis & Celery (for scheduling), WeasyPrint (PDFs).
- **Core Modules (Apps):** `customers`, `drivers`, `shipments`, `vehicles`.
- **Project Structure:**
  - `d:\Projects\FilaTrucking\` - Repository Root
    - `FilaTrucking/` - Django Source Root (contains `manage.py`)
      - `FilaTrucking/` - Project configuration (`settings.py`, `urls.py`)
      - `customers/`, `drivers/`, `shipments/`, `vehicles/` - Django Apps
      - `templates/` - Global HTML templates
      - `static/` - CSS, JS, images
      - `media/` - Uploaded files (receipts, PDFs)
    - `.venv/` - Python Virtual Environment
    - `docs/PRD.md` - Primary Business Logic Documentation (MUST READ)
    - `requirements.txt` - Python Dependencies (UTF-16LE encoded)

## 2. Setup & Environment Commands
All Django-related commands must be executed from the Django source root (`FilaTrucking/`).

### 2.1 Virtual Environment (Windows)
```powershell
# Activate venv
.venv\Scripts\activate

# Install dependencies if updated
pip install -r ..\requirements.txt
```

### 2.2 Database Operations
The project uses MySQL on `localhost:3306`. Ensure MySQL is running before executing:
```powershell
python manage.py makemigrations  # Create migration files for app changes
python manage.py migrate         # Apply pending migrations to MySQL
python manage.py createsuperuser # Create an admin user for /admin/
```

### 2.3 Local Development
```powershell
python manage.py runserver       # Start Django dev server (default: port 8000)
python manage.py collectstatic   # Collect static files into STATIC_ROOT
```

## 3. Linting & Formatting

Standardize code using these tools before submitting changes:
- `black .` - Automatic code formatting (PEP 8).
- `flake8 .` - Syntax and style error checking.
- `isort .`  - Standardized import sorting.

## 4. Code Style & Standards

### 4.1 Naming Conventions
- **Classes:** `PascalCase` (e.g., `ShipmentListView`, `InvoiceRecord`).
- **Functions/Parameters/Variables:** `snake_case` (e.g., `total_amount`, `calculate_total()`).
- **Template Files:** `<app>/<model>_<action>.html` (e.g., `shipments/shipment_form.html`).
- **Database Tables:** Standard Django `app_modelname` naming.

### 4.2 Django Patterns
- **Models:**
  - Explicit `on_delete` for `ForeignKey` fields (default to `CASCADE`).
  - Use `verbose_name` for user-visible field labels.
  - Implement `__str__(self)` for readable admin/debug logs.
- **Views:**
  - Default to **Class-Based Views (CBVs)** for standard CRUD.
  - Use `LoginRequiredMixin` for all non-public views.
  - Pass descriptive `context_object_name` in List/Detail views.
- **Templates:**
  - Use `{% extends "base.html" %}`.
  - Use `{% url 'name' %}` for all internal links.

### 4.3 Error Handling & Typing
- **Resource Lookups:** Always use `get_object_or_404(Model, pk=...)`.
- **Validation:** Perform logic in `forms.py` or `models.clean()`.
- **Typing:** Use Python 3.10+ type hints where it improves code readability.

## 5. Core Business Processes (PRD Highlights)

### 5.1 Shipment Lifecycle
- **Statuses:** `pending_review` (from WhatsApp) -> `confirmed` (by Admin) -> `invoiced` (linked to Invoice).
- **Ingestion:** Drivers send formatted WhatsApp messages; parsed by a Node.js/Baileys sidecar and POSTed to Django.

### 5.2 Invoice Generation
- Invoices group multiple `confirmed` shipments for each customer.
- Format must strictly follow `invoice_91.pdf` reference.
- PDFs generated server-side (WeasyPrint) and emailed via Django SMTP.

### 5.3 Financial Reporting
- **IFTA:** Manual mileage entry per state; system calculates tax using stored rates (updated quarterly).
- **Monthly Statements:** Auto-generated on the 1st; aggregates revenue, expenses, and driver pay.

### 5.4 External Integrations
- **WhatsApp Web (Baileys):** Listens for driver messages. Rest endpoint in Django expects a shared secret key.
- **GoMotive API:** REST integration for vehicle mileage/odometer sync. Runs via Celery beat or management commands.

## 6. Common Workflows

### 6.1 Adding a New App Feature
1. Define the model in `<app>/models.py`.
2. Run `python manage.py makemigrations` and `python manage.py migrate`.
3. Create a ModelForm in `<app>/forms.py`.
4. Create CRUD views in `<app>/views.py` (prefer CBVs).
5. Map URLs in `<app>/urls.py` and include them in `FilaTrucking/urls.py`.
6. Create templates in `FilaTrucking/templates/<app>/`.
7. Add a unit test in `<app>/tests.py`.

### 6.2 Handling Manual IFTA Entry
- Admin enters mileage per state for a vehicle/quarter.
- System looks up `IFTARate` for that state/quarter.
- `Shipment` records are NOT used for IFTA calculation; it relies on manual `IFTAMileage` entries (as per PRD).

### 6.3 Debugging Template Issues
- Check `settings.py` -> `TEMPLATES` -> `'DIRS'` to ensure global templates are included.
- Use `django-debug-toolbar` (if installed) or check the console output for template resolution errors.
- Ensure `STATICFILES_FINDERS` includes `AppDirectoriesFinder`.

## 7. UI & Frontend Standards

All templates must follow these standards. Never use Django's default admin styling or unstyled HTML for the custom frontend.

### 7.1 Tech Stack
- **CSS Framework:** Tailwind CSS via CDN (`<script src="https://cdn.tailwindcss.com"></script>`). No build step required.
- **Icons:** Heroicons via CDN or inline SVG only. No Font Awesome.
- **JS:** Vanilla JS or Alpine.js (`x-data`, `x-show`, `@click`) for lightweight interactivity. No jQuery.

### 7.2 Design Tokens (apply via Tailwind config block in base.html)
```html
<script>
  tailwind.config = {
    theme: {
      extend: {
        colors: {
          brand:   { DEFAULT: '#1B3A6B', light: '#2A5298' },  /* deep navy */
          accent:  { DEFAULT: '#F59E0B', dark: '#D97706' },   /* amber */
          surface: { DEFAULT: '#0F172A', card: '#1E293B' },   /* dark bg */
          muted:   '#64748B',
        },
        fontFamily: {
          sans: ['DM Sans', 'sans-serif'],
          mono: ['JetBrains Mono', 'monospace'],
        },
      }
    }
  }
</script>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
```

**Palette rationale:** Dark navy surface suits a trucking ops dashboard (serious, legible, low eye-strain on long shifts). Amber accent provides high-contrast CTAs without feeling like a generic SaaS tool.

### 7.3 base.html Structure
```
<html>
  <head>  ← meta viewport, Tailwind CDN, fonts, config block </head>
  <body class="bg-surface text-slate-100 min-h-screen font-sans">
    ├── <nav>          ← top bar: logo + hamburger (mobile) + nav links
    ├── <aside>        ← sidebar (hidden on mobile, slide-in drawer on mobile)
    └── <main>         ← {% block content %}
          └── page-level <div class="p-4 md:p-8">
```

- Sidebar collapses to a bottom tab bar on screens `< md` (640 px).
- Always include `<meta name="viewport" content="width=device-width, initial-scale=1">`.

### 7.4 Component Patterns

**Cards** (shipment rows, stat blocks):
```html
<div class="bg-surface-card rounded-2xl p-4 shadow-lg border border-slate-700/50">
```

**Primary button:**
```html
<button class="bg-accent hover:bg-accent-dark text-surface font-semibold px-4 py-2 rounded-xl transition-colors">
```

**Status badges** — use inline pill spans, never raw text:
| Status | Classes |
|---|---|
| `pending_review` | `bg-yellow-500/20 text-yellow-300 border border-yellow-500/40` |
| `confirmed` | `bg-blue-500/20 text-blue-300 border border-blue-500/40` |
| `invoiced` | `bg-green-500/20 text-green-300 border border-green-500/40` |
| `draft` | `bg-slate-500/20 text-slate-300 border border-slate-500/40` |
| `sent` | `bg-purple-500/20 text-purple-300 border border-purple-500/40` |
| `paid` | `bg-emerald-500/20 text-emerald-300 border border-emerald-500/40` |

**Tables** (shipment list, expense list):
- Use `overflow-x-auto` wrapper on mobile.
- Sticky first column on wide tables: `sticky left-0 bg-surface-card`.
- Alternate rows: `odd:bg-surface even:bg-surface-card`.

**Forms:**
```html
<label class="block text-sm text-muted mb-1">Field Label</label>
<input class="w-full bg-slate-800 border border-slate-600 rounded-xl px-3 py-2 text-slate-100
              focus:outline-none focus:ring-2 focus:ring-accent focus:border-transparent">
```

**Dashboard stat card:**
```html
<div class="bg-surface-card rounded-2xl p-5 border border-slate-700/50">
  <p class="text-muted text-xs uppercase tracking-widest">Gross Revenue</p>
  <p class="text-3xl font-bold text-slate-100 mt-1">$24,800</p>
  <p class="text-green-400 text-sm mt-1">↑ 12% vs last month</p>
</div>
```

### 7.5 Mobile Rules
- Every page must be fully usable on a 390 px wide screen.
- No horizontal scroll except inside explicitly wrapped tables.
- Tap targets minimum `44×44 px` (`min-h-[44px] min-w-[44px]`).
- Modals/drawers use `fixed inset-0` overlay + `translate-y` or `translate-x` transition.
- Filter/search bars collapse behind a "Filters" toggle button on mobile.

### 7.6 Navigation (Sidebar)
Sidebar links in order:
1. Dashboard
2. Shipments
3. Invoices
4. Drivers
5. Vehicles
6. Expenses
7. Reports (IFTA, Statements)
8. Settings

On mobile: render as a bottom sheet drawer triggered by a hamburger icon in the top bar.

### 7.7 Page Title Convention
Every page must have a consistent header block inside `{% block content %}`:
```html
<div class="flex items-center justify-between mb-6">
  <h1 class="text-2xl font-bold tracking-tight">Page Title</h1>
  <a href="..." class="bg-accent ...">+ Add New</a>  {# only where applicable #}
</div>
```

### 7.8 Empty States
Never show a blank page or raw "No results." Every list view must have an empty-state block:
```html
<div class="text-center py-16 text-muted">
  <svg .../>  {# relevant Heroicon #}
  <p class="mt-3 font-medium">No shipments yet</p>
  <p class="text-sm mt-1">Create one manually or wait for a WhatsApp message.</p>
</div>
```

### 7.9 Alerts & Flash Messages
Map Django message levels to styled banners at the top of `<main>`:
| Level | Classes |
|---|---|
| `success` | `bg-green-500/15 border-green-500/40 text-green-300` |
| `error` | `bg-red-500/15 border-red-500/40 text-red-300` |
| `warning` | `bg-yellow-500/15 border-yellow-500/40 text-yellow-300` |
| `info` | `bg-blue-500/15 border-blue-500/40 text-blue-300` |

## 8. Development Tips & Troubleshooting
- **Manage.py Location:** Always run from `D:\Projects\FilaTrucking\FilaTrucking`.
- **Database:** Local MySQL credentials are in `settings.py`.
- **Media Files:** Served locally from `FilaTrucking/media/`.
- **Celery:** Not yet fully implemented, but tasks should be defined in `tasks.py` within apps.
- **Reference Docs:** Refer to `docs/PRD.md` for the single source of truth on business logic and field requirements.