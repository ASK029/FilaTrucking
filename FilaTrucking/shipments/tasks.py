from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from celery import shared_task
from django.db import transaction
from django.db.models import Sum

from .models import Expense, ExpenseCategory, FinancialStatement, Invoice, InvoiceLineItem, InvoiceStatus, Shipment, ShipmentStatus


def _previous_month(today: date) -> tuple[int, int]:
    return (today.year - 1, 12) if today.month == 1 else (today.year, today.month - 1)


def _period_figures(start_date: date, end_date: date) -> dict:
    """Revenue counts PAID invoices by paid_at (PRD); driver pay is the DRIVER expense category."""
    revenue = (
        Invoice.objects.filter(status=InvoiceStatus.PAID, paid_at__range=(start_date, end_date))
        .aggregate(total=Sum("total_amount"))["total"] or Decimal("0")
    )
    expenses = Expense.objects.filter(date__range=(start_date, end_date))
    total_expenses = expenses.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    driver_pay_total = (
        expenses.filter(category=ExpenseCategory.DRIVER).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    )
    cents = Decimal("0.01")  # databases differ in what SUM() returns ('300' vs '300.00'): always report cents
    return {
        "revenue": revenue.quantize(cents),
        "total_expenses": total_expenses.quantize(cents),
        "driver_pay_total": driver_pay_total.quantize(cents),
        "net_profit": (revenue - total_expenses).quantize(cents),
    }


@shared_task
def generate_monthly_statement(year: int | None = None, month: int | None = None) -> dict:
    """Compute and store the financial figures of one month (QA-009).

    Called by Celery beat with no arguments on the 1st, it reports the month that just ended (the old code
    reported the brand-new, empty month). Pass year/month explicitly to (re)generate any period.
    The result is saved as a FinancialStatement row (one per month, updated on re-run) and returned as a dict.
    """
    if year is None and month is None:
        year, month = _previous_month(date.today())
    else:
        today = date.today()
        year = int(year or today.year)
        month = int(month or today.month)

    _, last_day = monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)
    figures = _period_figures(start_date, end_date)

    FinancialStatement.objects.update_or_create(
        period_type=FinancialStatement.PeriodType.MONTHLY, year=year, month=month,
        defaults={"start_date": start_date, "end_date": end_date, **figures},
    )

    return {
        "year": year,
        "month": month,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        **{k: str(v) for k, v in figures.items()},
    }


@shared_task
def generate_yearly_statement(year: int | None = None) -> dict:
    """Compute and store the financial figures of one year; with no argument: the year that just ended."""
    year = int(year) if year else date.today().year - 1

    start_date = date(year, 1, 1)
    end_date = date(year, 12, 31)
    figures = _period_figures(start_date, end_date)

    FinancialStatement.objects.update_or_create(
        period_type=FinancialStatement.PeriodType.YEARLY, year=year, month=0,
        defaults={"start_date": start_date, "end_date": end_date, **figures},
    )

    return {
        "year": year,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "total_revenue": str(figures["revenue"]),
        "total_expenses": str(figures["total_expenses"]),
        "driver_pay_total": str(figures["driver_pay_total"]),
        "net_profit": str(figures["net_profit"]),
    }


@shared_task
def generate_recurring_invoices() -> int:
    """Generate draft invoices for customers based on confirmed shipments in the last period.

    This is a simplified recurring invoice mechanism:
    - For each customer that has confirmed shipments in the previous month but no invoice yet,
      create a draft invoice and line items for those shipments.
    - Mark the shipments as invoiced after creating the line items.
    """
    today = date.today()
    # Previous month period
    if today.month == 1:
        period_year = today.year - 1
        period_month = 12
    else:
        period_year = today.year
        period_month = today.month - 1

    _, last_day = monthrange(period_year, period_month)
    start_date = date(period_year, period_month, 1)
    end_date = date(period_year, period_month, last_day)

    shipments_qs = Shipment.objects.filter(
        status=ShipmentStatus.CONFIRMED,
        date__range=(start_date, end_date),
    ).select_related("customer")

    created_invoices = 0

    # Group shipments by customer
    shipments_by_customer: dict[int, list[Shipment]] = {}
    for shipment in shipments_qs:
        shipments_by_customer.setdefault(shipment.customer_id, []).append(shipment)

    for customer_id, shipments in shipments_by_customer.items():
        # Skip if an invoice already exists for this customer and period
        existing_invoice = Invoice.objects.filter(
            customer_id=customer_id,
            invoice_date__range=(start_date, end_date),
        ).first()
        if existing_invoice:
            continue

        with transaction.atomic():
            invoice = Invoice.objects.create(
                customer_id=customer_id,
                invoice_date=today,
            )

            for s in shipments:
                InvoiceLineItem.objects.create(
                    invoice=invoice,
                    shipment=s,
                    date_incurred=s.date,
                    description=f"Shipment {s.container}",
                    booking_no=s.booking,
                    container_no=s.container,
                    seal_no=s.seal,
                    location=s.location,
                    amount=s.amount,
                )
                s.status = ShipmentStatus.INVOICED
                s.save(update_fields=["status"])

            invoice.calculate_total()
            created_invoices += 1

    return created_invoices


@shared_task
def send_ifta_deadline_reminders() -> dict:
    """Create simple in-app reminders when IFTA deadlines are within 14 days.

    This task returns which deadlines are currently within the reminder window.
    """
    today = date.today()
    current_year = today.year

    # Quarterly deadlines per PRD (Jan 31, Apr 30, Jul 31, Oct 31)
    deadlines = [
        date(current_year, 1, 31),
        date(current_year, 4, 30),
        date(current_year, 7, 31),
        date(current_year, 10, 31),
    ]

    window_days = 14
    upcoming = []

    for d in deadlines:
        delta = (d - today).days
        if 0 <= delta <= window_days:
            upcoming.append(d.isoformat())

    return {"today": today.isoformat(), "upcoming_deadlines": upcoming}

