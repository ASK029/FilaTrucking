from datetime import date

from django.test import TestCase

from .models import Expense, ExpenseCategory, Invoice, InvoiceLineItem, Shipment, ShipmentStatus
from .tasks import (
    generate_monthly_statement,
    generate_recurring_invoices,
    generate_yearly_statement,
    send_ifta_deadline_reminders,
)


class FinancialTasksTests(TestCase):
    def setUp(self) -> None:
        self.invoice = Invoice.objects.create(
            customer_id=None,  # type: ignore[arg-type]
            invoice_date=date(2026, 1, 15),
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

        Expense.objects.create(
            date=date(2026, 1, 5),
            category=ExpenseCategory.DRIVER_PAY,
            amount=40,
        )

    def test_generate_monthly_statement(self) -> None:
        result = generate_monthly_statement(year=2026, month=1)
        self.assertEqual(result["year"], 2026)
        self.assertEqual(result["month"], 1)
        self.assertEqual(result["revenue"], "100")
        self.assertEqual(result["driver_pay_total"], "40")

    def test_generate_yearly_statement(self) -> None:
        result = generate_yearly_statement(year=2026)
        self.assertEqual(result["year"], 2026)
        self.assertEqual(result["total_revenue"], "100")
        self.assertEqual(result["total_expenses"], "40")

    def test_generate_recurring_invoices_no_confirmed_shipments(self) -> None:
        created = generate_recurring_invoices()
        self.assertEqual(created, 0)

    def test_ifta_deadline_reminders_window(self) -> None:
        result = send_ifta_deadline_reminders()
        self.assertIn("today", result)
        self.assertIn("upcoming_deadlines", result)
