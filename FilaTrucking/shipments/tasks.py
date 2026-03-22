from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta
from decimal import Decimal

from celery import shared_task
from django.db import transaction
from django.db.models import Sum

from .models import Expense, ExpenseCategory, Invoice, InvoiceLineItem, Shipment, ShipmentStatus


@shared_task
def generate_monthly_statement(year: int | None = None, month: int | None = None) -> dict:
    """Compute monthly financial aggregates for the given period.

    Returns a lightweight dict so the task result can be inspected in Celery.
    """
    today = date.today()
    year = int(year or today.year)
    month = int(month or today.month)

    _, last_day = monthrange(year, month)
    start_date = date(year, month, 1)
    end_date = date(year, month, last_day)

    invoices = Invoice.objects.filter(invoice_date__range=(start_date, end_date))
    revenue = invoices.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")

    expenses = Expense.objects.filter(date__range=(start_date, end_date))
    total_expenses = expenses.aggregate(total=Sum("amount"))["total"] or Decimal("0")

    driver_pay_total = (
        expenses.filter(category=ExpenseCategory.DRIVER_PAY)
        .aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )

    net_profit = revenue - total_expenses

    return {
        "year": year,
        "month": month,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "revenue": str(revenue),
        "total_expenses": str(total_expenses),
        "driver_pay_total": str(driver_pay_total),
        "net_profit": str(net_profit),
    }


@shared_task
def generate_yearly_statement(year: int | None = None) -> dict:
    """Compute yearly financial aggregates for the given year."""
    today = date.today()
    year = int(year or today.year)

    start_date = date(year, 1, 1)
    end_date = date(year, 12, 31)

    invoices = Invoice.objects.filter(invoice_date__range=(start_date, end_date))
    total_revenue = invoices.aggregate(total=Sum("total_amount"))["total"] or Decimal("0")

    expenses = Expense.objects.filter(date__range=(start_date, end_date))
    total_expenses = expenses.aggregate(total=Sum("amount"))["total"] or Decimal("0")

    net_profit = total_revenue - total_expenses

    return {
        "year": year,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "total_revenue": str(total_revenue),
        "total_expenses": str(total_expenses),
        "net_profit": str(net_profit),
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

