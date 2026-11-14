from datetime import date
from decimal import Decimal

from django.test import TestCase

from customers.models import Customer

from .models import Expense, ExpenseCategory, Invoice, InvoiceLineItem, InvoiceStatus
from .tasks import (
    generate_monthly_statement,
    generate_recurring_invoices,
    generate_yearly_statement,
    send_ifta_deadline_reminders,
)


class FinancialTasksTests(TestCase):
    """Basic contract of the Celery tasks. (The old fixtures used customer_id=None and unpaid invoices, so the tests
    never matched the PRD rules: statements count PAID invoices by paid_at.) More cases: qa/tests."""

    def setUp(self) -> None:
        customer = Customer.objects.create(
            name="Test Customer", abbreviation="TC", phone_number="+13125550100",
            address="1 Main St", email="ap@example.com",
        )
        self.invoice = Invoice.objects.create(
            customer=customer,
            invoice_date=date(2026, 1, 15),
            status=InvoiceStatus.PAID,
            paid_at=date(2026, 1, 20),
        )
        InvoiceLineItem.objects.create(
            invoice=self.invoice,
            shipment=None,
            date_incurred=date(2026, 1, 10),
            description="Test line",
            container_no="ABC",
            seal_no="123",
            location="CHI",
            amount=100,
        )
        self.invoice.calculate_total()

        Expense.objects.create(date=date(2026, 1, 5), category=ExpenseCategory.TRUCK, amount=40)
        Expense.objects.create(date=date(2026, 1, 6), category=ExpenseCategory.DRIVER, amount=25)

    def test_generate_monthly_statement(self) -> None:
        result = generate_monthly_statement(year=2026, month=1)
        self.assertEqual(result["year"], 2026)
        self.assertEqual(result["month"], 1)
        self.assertEqual(Decimal(result["revenue"]), Decimal("100"))
        self.assertEqual(Decimal(result["total_expenses"]), Decimal("65"))
        self.assertEqual(Decimal(result["driver_pay_total"]), Decimal("25"))
        self.assertEqual(Decimal(result["net_profit"]), Decimal("35"))

    def test_generate_yearly_statement(self) -> None:
        result = generate_yearly_statement(year=2026)
        self.assertEqual(result["year"], 2026)
        self.assertEqual(Decimal(result["total_revenue"]), Decimal("100"))
        self.assertEqual(Decimal(result["total_expenses"]), Decimal("65"))

    def test_generate_recurring_invoices_no_confirmed_shipments(self) -> None:
        created = generate_recurring_invoices()
        self.assertEqual(created, 0)

    def test_ifta_deadline_reminders_window(self) -> None:
        result = send_ifta_deadline_reminders()
        self.assertIn("today", result)
        self.assertIn("upcoming_deadlines", result)
