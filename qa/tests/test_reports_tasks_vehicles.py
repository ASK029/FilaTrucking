"""Scheduled tasks, statements, vehicles/IFTA/GoMotive."""
from datetime import date
from decimal import Decimal
from unittest import mock

from shipments import tasks
from shipments.models import Expense, ExpenseCategory, Invoice, InvoiceStatus, Shipment, ShipmentStatus
from vehicles.models import IFTAMileage, Maintenance, Vehicle

from .base import QABase


def frozen(day):
    class FakeDate(date):
        @classmethod
        def today(cls):
            return day
    return FakeDate


class StatementTasks(QABase):
    def setUp(self):
        super().setUp()
        Invoice.objects.create(customer=self.customer, invoice_date=date(2026, 1, 10), status=InvoiceStatus.PAID,
                               paid_at=date(2026, 1, 12), total_amount=Decimal("400"))
        Expense.objects.create(date=date(2026, 1, 5), category=ExpenseCategory.FUEL, amount=Decimal("100"))

    def test_monthly_statement_task_computes(self):
        """[TASK-NAMEERROR] tasks.py:46 uses `driver_pay_total`, which is never defined."""
        r = tasks.generate_monthly_statement(year=2026, month=1)
        self.assertEqual((r["revenue"], r["total_expenses"], r["net_profit"]), ("400.00", "100.00", "300.00"))

    def test_scheduled_monthly_run_reports_the_month_that_just_ended(self):
        """[TASK-PERIOD] beat fires on the 1st at 01:00 with no args; the task then reports the *new, empty* month."""
        with mock.patch("shipments.tasks.date", frozen(date(2026, 2, 1))):
            try:
                r = tasks.generate_monthly_statement()
            except NameError:
                self.fail("task crashes first (see TASK-NAMEERROR); period logic cannot even be evaluated")
        self.assertEqual((r["year"], r["month"]), (2026, 1))

    def test_scheduled_yearly_run_reports_the_year_that_just_ended(self):
        with mock.patch("shipments.tasks.date", frozen(date(2027, 1, 1))):
            r = tasks.generate_yearly_statement()
        self.assertEqual(r["year"], 2026)

    def test_statement_view_renders_figures(self):
        self.login()
        r = self.client.get("/reports/financial/monthly/?year=2026&month=1")
        self.assertEqual((r.status_code, r.context["revenue"], r.context["net_profit"]),
                         (200, Decimal("400.00"), Decimal("300.00")))

    def test_statement_view_rejects_bad_month(self):
        """[RPT-PARAM] ?month=13 / ?month=abc -> ValueError -> 500."""
        self.login()
        for q in ("month=13", "month=abc", "year=99999"):
            with self.subTest(q=q):
                self.assertEqual(self.client.get(f"/reports/financial/monthly/?{q}").status_code, 400)


class RecurringInvoices(QABase):
    def make(self, amount):
        return Shipment.objects.create(customer=self.customer, date=date(2026, 1, 5), booking="B", container="C",
                                       seal="S", location="CHI", amount=amount, status=ShipmentStatus.CONFIRMED)

    def test_shipment_without_amount_does_not_abort_the_whole_run(self):
        """[TASK-NULL-AMOUNT] Shipment.amount is nullable, InvoiceLineItem.amount is not -> IntegrityError, task dies."""
        self.make(None)
        with mock.patch("shipments.tasks.date", frozen(date(2026, 2, 3))):
            tasks.generate_recurring_invoices()

    def test_creates_one_draft_and_marks_shipments_invoiced(self):
        s = self.make(Decimal("400"))
        with mock.patch("shipments.tasks.date", frozen(date(2026, 2, 3))):
            self.assertEqual(tasks.generate_recurring_invoices(), 1)
            self.assertEqual(tasks.generate_recurring_invoices(), 0)  # idempotent
        s.refresh_from_db()
        self.assertEqual(s.status, ShipmentStatus.INVOICED)
        self.assertEqual(Invoice.objects.get().status, InvoiceStatus.DRAFT)


class Vehicles(QABase):
    def test_sync_for_vehicle_without_gomotive_id_shows_a_message(self):
        """[VEH-NAME] vehicles/views.py reads `vehicle.name`, a field that was removed -> AttributeError -> 500."""
        self.login()
        r = self.client.post(f"/vehicles/{self.vehicle.pk}/sync/")
        self.assertEqual(r.status_code, 302)

    def test_maintenance_service_date_is_editable(self):
        """[VEH-DATE] Maintenance.date is auto_now_add, so historical services cannot be entered with their real date."""
        self.assertTrue(Maintenance._meta.get_field("date").editable)

    def test_ifta_miles_entry_upserts(self):
        self.login()
        data = {"vehicle": self.vehicle.pk, "state_code": "IL", "month": 1, "year": 2026, "miles": "100"}
        self.client.post("/vehicles/ifta/log/miles/", data)
        self.client.post("/vehicles/ifta/log/miles/", dict(data, miles="150"))
        self.assertEqual(list(IFTAMileage.objects.values_list("miles", flat=True)), [Decimal("150.00")])

    def test_ifta_report_bad_params_do_not_500(self):
        self.login()
        self.assertEqual(self.client.get("/vehicles/ifta/report/?year=abc&month=13").status_code, 200)

    def test_deleting_a_vehicle_keeps_ifta_history(self):
        """[DATA-CASCADE] Vehicle -> IFTAMileage / Maintenance are CASCADE; deleting a truck erases its IFTA + service history."""
        IFTAMileage.objects.create(vehicle=self.vehicle, state_code="IL", month=1, year=2026, miles=1)
        self.login()
        self.client.post(f"/vehicles/{self.vehicle.pk}/delete/")
        self.assertTrue(IFTAMileage.objects.exists())


class StatementPersistenceQA009(QABase):
    def test_monthly_task_stores_a_statement_row_and_includes_driver_pay(self):
        from shipments.models import FinancialStatement
        Invoice.objects.create(customer=self.customer, invoice_date=date(2026, 1, 10), status=InvoiceStatus.PAID,
                               paid_at=date(2026, 1, 12), total_amount=Decimal("900"))
        Expense.objects.create(date=date(2026, 1, 5), category=ExpenseCategory.DRIVER, amount=Decimal("300"), driver=self.driver)
        Expense.objects.create(date=date(2026, 1, 6), category=ExpenseCategory.FUEL, amount=Decimal("100"))
        Expense.objects.create(date=date(2026, 2, 1), category=ExpenseCategory.DRIVER, amount=Decimal("999"))  # other month
        r = tasks.generate_monthly_statement(2026, 1)
        self.assertEqual((r["driver_pay_total"], r["total_expenses"], r["net_profit"]), ("300.00", "400.00", "500.00"))
        row = FinancialStatement.objects.get(period_type="monthly", year=2026, month=1)
        self.assertEqual((row.revenue, row.driver_pay_total, row.net_profit), (Decimal("900.00"), Decimal("300.00"), Decimal("500.00")))
        tasks.generate_monthly_statement(2026, 1)  # re-run updates, never duplicates
        self.assertEqual(FinancialStatement.objects.count(), 1)

    def test_beat_run_in_january_reports_december_of_the_previous_year(self):
        with mock.patch("shipments.tasks.date", frozen(date(2027, 1, 1))):
            r = tasks.generate_monthly_statement()
        self.assertEqual((r["year"], r["month"]), (2026, 12))

    def test_explicit_period_is_respected_and_current_month_is_the_fallback_for_one_missing_arg(self):
        with mock.patch("shipments.tasks.date", frozen(date(2026, 5, 20))):
            self.assertEqual(tasks.generate_monthly_statement(2025, 3)["month"], 3)
            self.assertEqual(tasks.generate_monthly_statement(month=4)["year"], 2026)

    def test_yearly_task_stores_its_row(self):
        from shipments.models import FinancialStatement
        with mock.patch("shipments.tasks.date", frozen(date(2027, 1, 1))):
            tasks.generate_yearly_statement()
        self.assertTrue(FinancialStatement.objects.filter(period_type="yearly", year=2026, month=0).exists())
