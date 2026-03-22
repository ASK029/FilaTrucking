from datetime import date
from calendar import month_name, monthrange

from decimal import Decimal

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Max, Sum
from django.shortcuts import render

from shipments.models import Expense, ExpenseCategory, Invoice, InvoiceStatus, Shipment, ShipmentStatus
from vehicles.models import Maintenance, Vehicle


@login_required
def dashboard(request):
    """Central dashboard with financial stats, shipment status, and maintenance alerts."""
    today = date.today()
    year = today.year
    month = today.month

    # Current month date range
    _, last_day = monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)

    # Revenue for current month (sum of invoiced totals in range)
    invoices = Invoice.objects.filter(invoice_date__range=(start_date, end_date))
    revenue = invoices.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")

    # Shipment stats
    active_shipments_count = Shipment.objects.filter(
        status=ShipmentStatus.CONFIRMED
    ).count()
    pending_review_count = Shipment.objects.filter(
        status=ShipmentStatus.PENDING_REVIEW
    ).count()

    # Pending invoices: drafts + sent
    open_invoices_count = Invoice.objects.filter(
        status__in=[InvoiceStatus.DRAFT, InvoiceStatus.SENT]
    ).count()

    # Maintenance alerts: vehicles within 500 miles of next service
    maintenance_alerts = []
    alert_threshold = 500

    # For each vehicle, look at the latest maintenance record with next_service_mileage
    latest_maintenance = (
        Maintenance.objects.filter(next_service_mileage__isnull=False)
        .values("vehicle_id")
        .annotate(latest_id=Max("id"))
    )
    latest_ids = [row["latest_id"] for row in latest_maintenance]
    maintenance_qs = (
        Maintenance.objects.filter(id__in=latest_ids)
        .select_related("vehicle")
        .order_by("vehicle__name")
    )

    for entry in maintenance_qs:
        vehicle = entry.vehicle
        if vehicle.current_odometer is None or entry.next_service_mileage is None:
            continue
        miles_to_service = entry.next_service_mileage - vehicle.current_odometer
        if miles_to_service <= alert_threshold:
            maintenance_alerts.append(
                {
                    "vehicle": vehicle,
                    "miles_to_service": miles_to_service,
                    "next_service_mileage": entry.next_service_mileage,
                    "last_service_mileage": entry.mileage_at_service,
                }
            )

    # Schedule overview from Celery beat schedule
    beat_schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {}) or {}
    schedule_rows = []
    label_map = {
        "monthly-financial-statement": "Monthly statement",
        "yearly-financial-statement": "Yearly statement",
        "recurring-invoices": "Recurring invoices",
        "ifta-deadline-reminders": "IFTA reminders",
        "gomotive-nightly-sync": "GoMotive nightly sync",
    }
    for key, value in beat_schedule.items():
        schedule_rows.append(
            {
                "key": key,
                "label": label_map.get(key, key.replace("-", " ").title()),
                "task": value.get("task"),
                "schedule": str(value.get("schedule")),
            }
        )

    context = {
        "revenue_this_month": revenue,
        "active_shipments_count": active_shipments_count,
        "pending_review_count": pending_review_count,
        "open_invoices_count": open_invoices_count,
        "maintenance_alerts": maintenance_alerts,
        "schedule_rows": schedule_rows,
    }
    return render(request, "dashboard.html", context)


# ── CSV-style category labels used in notes field during import ──
# (note_label, snake_case_key, display_label)
CSV_CATEGORIES = [
    ("IRP", "irp", "IRP"),
    ("Parking", "parking", "Parking"),
    ("On Site", "on_site", "On Site"),
    ("Truck", "truck", "Truck"),
    ("Check Charge", "check_charge", "Check Charge"),
    ("Insurance", "insurance", "Insurance"),
    ("Toll", "toll", "Toll"),
    ("Fuel", "fuel", "Fuel"),
    ("Other", "other_exp", "Other"),
    ("Chassis", "chassis", "Chassis"),
]


def _sum_by_note_label(expenses_qs, label: str) -> Decimal:
    """Sum expenses whose notes field contains a specific imported label."""
    return (
        expenses_qs.filter(notes__icontains=f"Imported: {label}")
        .aggregate(t=Sum("amount"))["t"]
        or Decimal("0")
    )


def _build_category_totals(expenses_qs) -> list[tuple[str, str, Decimal]]:
    """Return [(display_label, snake_key, value), ...] for each CSV category."""
    return [
        (display, key, _sum_by_note_label(expenses_qs, note_label))
        for note_label, key, display in CSV_CATEGORIES
    ]


def _category_dict(expenses_qs) -> dict[str, Decimal]:
    """Return {snake_key: value} for template row merging."""
    return {
        key: _sum_by_note_label(expenses_qs, note_label)
        for note_label, key, _ in CSV_CATEGORIES
    }


@login_required
def monthly_statement(request):
    """Monthly financial statement matching the CSV layout."""
    today = date.today()
    year = int(request.GET.get("year", today.year))
    month = int(request.GET.get("month", today.month))

    _, last_day = monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)

    # Revenue from invoices (deposits)
    invoices = Invoice.objects.filter(invoice_date__range=(start_date, end_date))
    revenue = invoices.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")

    # Expenses
    expenses = Expense.objects.filter(date__range=(start_date, end_date))
    total_expenses = expenses.aggregate(total=Sum("amount"))["total"] or Decimal("0")

    # Granular category breakdown (CSV columns)
    category_totals = _build_category_totals(expenses)

    # Driver pay breakdown
    driver_pay = (
        expenses.filter(category=ExpenseCategory.DRIVER_PAY)
        .aggregate(t=Sum("amount"))["t"]
        or Decimal("0")
    )
    driver_pay_breakdown = (
        expenses.filter(category=ExpenseCategory.DRIVER_PAY)
        .values("driver__id", "driver__name")
        .annotate(total=Sum("amount"))
        .order_by("driver__name")
    )

    net_profit = revenue - total_expenses

    # Transaction log: daily rows
    all_dates = sorted(
        set(expenses.values_list("date", flat=True))
        | set(invoices.values_list("invoice_date", flat=True))
    )
    transaction_rows = []
    for d in all_dates:
        day_invoices = invoices.filter(invoice_date=d)
        deposit_total = day_invoices.aggregate(t=Sum("total_amount"))["t"] or Decimal("0")
        deposit_from = ", ".join(
            day_invoices.values_list("customer__name", flat=True).distinct()
        ) or "-"

        day_expenses = expenses.filter(date=d)
        row = {"date": d, "deposit": deposit_total, "deposit_from": deposit_from}
        for note_label, key, _ in CSV_CATEGORIES:
            row[key] = _sum_by_note_label(day_expenses, note_label)
        day_pay = (
            day_expenses.filter(category=ExpenseCategory.DRIVER_PAY)
            .aggregate(t=Sum("amount"))["t"]
            or Decimal("0")
        )
        pay_to = ", ".join(
            day_expenses.filter(category=ExpenseCategory.DRIVER_PAY)
            .exclude(driver__isnull=True)
            .values_list("driver__name", flat=True)
            .distinct()
        ) or "-"
        row["pay"] = day_pay
        row["pay_to"] = pay_to
        transaction_rows.append(row)

    month_choices = [(m, month_name[m]) for m in range(1, 13)]

    context = {
        "year": year,
        "month": month,
        "start_date": start_date,
        "end_date": end_date,
        "revenue": revenue,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
        "category_totals": category_totals,
        "driver_pay": driver_pay,
        "driver_pay_breakdown": driver_pay_breakdown,
        "transaction_rows": transaction_rows,
        "month_choices": month_choices,
    }
    return render(request, "reports/monthly_statement.html", context)


@login_required
def yearly_statement(request):
    """Yearly financial statement – per-month per-category grid matching the CSV."""
    today = date.today()
    year = int(request.GET.get("year", today.year))

    start_date = date(year, 1, 1)
    end_date = date(year, 12, 31)

    invoices = Invoice.objects.filter(invoice_date__range=(start_date, end_date))
    total_revenue = invoices.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")

    expenses = Expense.objects.filter(date__range=(start_date, end_date))
    total_expenses = expenses.aggregate(total=Sum("amount"))["total"] or Decimal("0")

    net_profit = total_revenue - total_expenses

    monthly_rows = []
    col_totals = {key: Decimal("0") for _, key, _ in CSV_CATEGORIES}
    col_totals["pay"] = Decimal("0")
    col_totals["deposit"] = Decimal("0")

    for m in range(1, 13):
        _, ld = monthrange(year, m)
        ms = date(year, m, 1)
        me = date(year, m, ld)
        m_exp = expenses.filter(date__range=(ms, me))
        m_inv = invoices.filter(invoice_date__range=(ms, me))

        deposit = m_inv.aggregate(t=Sum("total_amount"))["t"] or Decimal("0")
        pay = (
            m_exp.filter(category=ExpenseCategory.DRIVER_PAY)
            .aggregate(t=Sum("amount"))["t"]
            or Decimal("0")
        )
        cats = _category_dict(m_exp)

        m_total_exp = m_exp.aggregate(t=Sum("amount"))["t"] or Decimal("0")

        row = {
            "month": m,
            "label": month_name[m],
            "deposit": deposit,
            "pay": pay,
            "total_expenses": m_total_exp,
            "profit": deposit - m_total_exp,
        }
        row.update(cats)
        monthly_rows.append(row)

        for _, key, _ in CSV_CATEGORIES:
            col_totals[key] += cats[key]
        col_totals["pay"] += pay
        col_totals["deposit"] += deposit

    context = {
        "year": year,
        "start_date": start_date,
        "end_date": end_date,
        "total_revenue": total_revenue,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
        "monthly_rows": monthly_rows,
        "col_totals": col_totals,
        "csv_categories": CSV_CATEGORIES,
    }
    return render(request, "reports/yearly_statement.html", context)


try:
    from weasyprint import HTML
except (ImportError, OSError):
    HTML = None


@login_required
def monthly_statement_pdf(request):
    """PDF export for the monthly statement."""
    if HTML is None:
        return render(
            request,
            "reports/monthly_statement_pdf.html",
            {"error": "WeasyPrint not installed. Cannot generate PDF."},
        )

    today = date.today()
    year = int(request.GET.get("year", today.year))
    month = int(request.GET.get("month", today.month))

    _, last_day = monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)

    invoices = Invoice.objects.filter(invoice_date__range=(start_date, end_date))
    revenue = invoices.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")

    expenses = Expense.objects.filter(date__range=(start_date, end_date))
    total_expenses = expenses.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    net_profit = revenue - total_expenses
    category_totals = _build_category_totals(expenses)

    driver_pay = (
        expenses.filter(category=ExpenseCategory.DRIVER_PAY)
        .aggregate(t=Sum("amount"))["t"]
        or Decimal("0")
    )
    driver_pay_breakdown = (
        expenses.filter(category=ExpenseCategory.DRIVER_PAY)
        .values("driver__id", "driver__name")
        .annotate(total=Sum("amount"))
        .order_by("driver__name")
    )

    context = {
        "year": year,
        "month": month,
        "start_date": start_date,
        "end_date": end_date,
        "revenue": revenue,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
        "category_totals": category_totals,
        "driver_pay": driver_pay,
        "driver_pay_breakdown": driver_pay_breakdown,
    }

    html_string = render(request, "reports/monthly_statement_pdf.html", context).content.decode(
        "utf-8"
    )
    html = HTML(string=html_string, base_url=request.build_absolute_uri("/"))
    pdf = html.write_pdf()

    from django.http import HttpResponse

    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="Monthly_Statement_{year}_{month:02d}.pdf"'
    )
    return response


@login_required
def yearly_statement_pdf(request):
    """PDF export for the yearly statement."""
    if HTML is None:
        return render(
            request,
            "reports/yearly_statement_pdf.html",
            {"error": "WeasyPrint not installed. Cannot generate PDF."},
        )

    today = date.today()
    year = int(request.GET.get("year", today.year))

    start_date = date(year, 1, 1)
    end_date = date(year, 12, 31)

    invoices = Invoice.objects.filter(invoice_date__range=(start_date, end_date))
    total_revenue = invoices.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")

    expenses = Expense.objects.filter(date__range=(start_date, end_date))
    total_expenses = expenses.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    net_profit = total_revenue - total_expenses

    monthly_rows = []
    col_totals = {key: Decimal("0") for _, key, _ in CSV_CATEGORIES}
    col_totals["pay"] = Decimal("0")
    col_totals["deposit"] = Decimal("0")

    for m in range(1, 13):
        _, ld = monthrange(year, m)
        ms = date(year, m, 1)
        me = date(year, m, ld)
        m_exp = expenses.filter(date__range=(ms, me))
        m_inv = invoices.filter(invoice_date__range=(ms, me))

        deposit = m_inv.aggregate(t=Sum("total_amount"))["t"] or Decimal("0")
        pay = (
            m_exp.filter(category=ExpenseCategory.DRIVER_PAY)
            .aggregate(t=Sum("amount"))["t"]
            or Decimal("0")
        )
        cats = _category_dict(m_exp)
        m_total_exp = m_exp.aggregate(t=Sum("amount"))["t"] or Decimal("0")

        row = {
            "month": m,
            "label": month_name[m],
            "deposit": deposit,
            "pay": pay,
            "total_expenses": m_total_exp,
            "profit": deposit - m_total_exp,
        }
        row.update(cats)
        monthly_rows.append(row)

        for _, key, _ in CSV_CATEGORIES:
            col_totals[key] += cats[key]
        col_totals["pay"] += pay
        col_totals["deposit"] += deposit

    context = {
        "year": year,
        "start_date": start_date,
        "end_date": end_date,
        "total_revenue": total_revenue,
        "total_expenses": total_expenses,
        "net_profit": net_profit,
        "monthly_rows": monthly_rows,
        "col_totals": col_totals,
        "csv_categories": CSV_CATEGORIES,
    }

    html_string = render(request, "reports/yearly_statement_pdf.html", context).content.decode(
        "utf-8"
    )
    html = HTML(string=html_string, base_url=request.build_absolute_uri("/"))
    pdf = html.write_pdf()

    from django.http import HttpResponse

    response = HttpResponse(pdf, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="Yearly_Statement_{year}.pdf"'
    return response
