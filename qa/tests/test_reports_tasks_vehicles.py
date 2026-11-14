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
        with mock.patch("shipments.tasks.today_local", return_value=date(2026, 2, 1)):
            try:
                r = tasks.generate_monthly_statement()
            except NameError:
                self.fail("task crashes first (see TASK-NAMEERROR); period logic cannot even be evaluated")
        self.assertEqual((r["year"], r["month"]), (2026, 1))

    def test_scheduled_yearly_run_reports_the_year_that_just_ended(self):
        with mock.patch("shipments.tasks.today_local", return_value=date(2027, 1, 1)):
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
        with mock.patch("shipments.tasks.today_local", return_value=date(2026, 2, 3)):
            tasks.generate_recurring_invoices()

    def test_creates_one_draft_and_marks_shipments_invoiced(self):
        s = self.make(Decimal("400"))
        with mock.patch("shipments.tasks.today_local", return_value=date(2026, 2, 3)):
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
        with mock.patch("shipments.tasks.today_local", return_value=date(2027, 1, 1)):
            r = tasks.generate_monthly_statement()
        self.assertEqual((r["year"], r["month"]), (2026, 12))

    def test_explicit_period_is_respected_and_current_month_is_the_fallback_for_one_missing_arg(self):
        with mock.patch("shipments.tasks.today_local", return_value=date(2026, 5, 20)):
            self.assertEqual(tasks.generate_monthly_statement(2025, 3)["month"], 3)
            self.assertEqual(tasks.generate_monthly_statement(month=4)["year"], 2026)

    def test_yearly_task_stores_its_row(self):
        from shipments.models import FinancialStatement
        with mock.patch("shipments.tasks.today_local", return_value=date(2027, 1, 1)):
            tasks.generate_yearly_statement()
        self.assertTrue(FinancialStatement.objects.filter(period_type="yearly", year=2026, month=0).exists())


class TimeZoneQA044(QABase):
    def test_business_time_zone_is_chicago(self):
        from django.conf import settings
        self.assertEqual(settings.TIME_ZONE, "America/Chicago")

    def test_today_is_the_chicago_date_not_utc(self):
        """At 02:30 UTC on 6 Jan it is still 5 Jan in Illinois: 'today' for the business must be the 5th."""
        from datetime import datetime, timezone as tz
        from django.utils import timezone
        with mock.patch("django.utils.timezone.now", return_value=datetime(2026, 1, 6, 2, 30, tzinfo=tz.utc)):
            self.assertEqual(tasks.today_local(), date(2026, 1, 5))
            self.assertEqual(timezone.localdate(), date(2026, 1, 5))

    def test_beat_run_just_after_local_midnight_on_the_first_reports_the_right_month(self):
        from datetime import datetime, timezone as tz
        # 1 Feb 00:30 in Chicago = 06:30 UTC; at 01:00 UTC on 1 Feb it is still 31 Jan in Chicago
        with mock.patch("django.utils.timezone.now", return_value=datetime(2026, 2, 1, 1, 0, tzinfo=tz.utc)):
            r = tasks.generate_monthly_statement()
        self.assertEqual((r["year"], r["month"]), (2025, 12))  # still January 31st locally -> reports December


class StatementCategoriesQA037(QABase):
    def setUp(self):
        super().setUp()
        self.login()
        # one typed-in expense (no "Imported:" note), one imported-style expense, one driver-pay row, other month
        Expense.objects.create(date=date(2026, 1, 5), category=ExpenseCategory.FUEL, amount=Decimal("100"), notes="typed in by hand")
        Expense.objects.create(date=date(2026, 1, 6), category=ExpenseCategory.FUEL, amount=Decimal("50"), notes="Imported: Pilot")
        Expense.objects.create(date=date(2026, 1, 7), category=ExpenseCategory.MAINTENANCE, amount=Decimal("30"))
        Expense.objects.create(date=date(2026, 1, 8), category=ExpenseCategory.DRIVER, amount=Decimal("200"), driver=self.driver)
        Expense.objects.create(date=date(2026, 2, 8), category=ExpenseCategory.FUEL, amount=Decimal("999"))

    def test_monthly_columns_group_by_category_not_by_note_text(self):
        r = self.client.get("/reports/financial/monthly/?year=2026&month=1")
        cols = {key: value for _, key, value in r.context["category_totals"]}
        self.assertEqual((cols["fuel"], cols["on_site"], cols["truck"]), (Decimal("150"), Decimal("30"), Decimal("0")))
        self.assertEqual(r.context["total_expenses"], Decimal("380"))

    def test_yearly_grid_uses_categories_too(self):
        r = self.client.get("/reports/financial/yearly/?year=2026")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["col_totals"]["fuel"], Decimal("1149"))

    def test_the_historical_importer_stores_driver_pay_as_driver(self):
        import inspect
        from shipments.management.commands import import_expenses
        src = inspect.getsource(import_expenses)
        self.assertIn("category=ExpenseCategory.DRIVER", src)

    def test_migration_reclassifies_old_import_rows_and_is_reversible(self):
        from importlib import import_module
        from django.apps import apps
        m = import_module("shipments.migrations.0014_reclassify_imported_driver_pay")
        pay = Expense.objects.create(date=date(2026, 1, 9), category=ExpenseCategory.TRUCK, amount=Decimal("75"), notes="Imported: Pay → Moe")
        truck = Expense.objects.create(date=date(2026, 1, 9), category=ExpenseCategory.TRUCK, amount=Decimal("20"), notes="Imported: Truck")
        m.to_driver(apps, None)
        pay.refresh_from_db(); truck.refresh_from_db()
        self.assertEqual((pay.category, truck.category), ("driver", "truck"))
        m.to_truck(apps, None)
        pay.refresh_from_db()
        self.assertEqual(pay.category, "truck")


class ProtectedHistoryQA039(QABase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_truck_with_history_cannot_be_deleted_and_the_user_is_told_why(self):
        IFTAMileage.objects.create(vehicle=self.vehicle, state_code="IL", month=1, year=2026, miles=1)
        Maintenance.objects.create(vehicle=self.vehicle, cost=1, service_provider="x", description="d")
        r = self.client.post(f"/vehicles/{self.vehicle.pk}/delete/", follow=True)
        self.assertTrue(Vehicle.objects.filter(pk=self.vehicle.pk).exists())
        self.assertEqual((IFTAMileage.objects.count(), Maintenance.objects.count()), (1, 1))
        self.assertEqual(r.redirect_chain[-1][0], f"/vehicles/{self.vehicle.pk}/")
        text = " ".join(str(m) for m in r.context["messages"])
        self.assertIn("Set its status to Retired", text)
        self.assertIn("IFTA", text)

    def test_truck_without_history_can_still_be_deleted(self):
        v = Vehicle.objects.create(registration_number="T-OLD", model="m", chassis_number="1OLD")
        self.assertEqual(self.client.post(f"/vehicles/{v.pk}/delete/").status_code, 302)
        self.assertFalse(Vehicle.objects.filter(pk=v.pk).exists())

    def test_driver_with_pay_history_cannot_be_deleted(self):
        Expense.objects.create(date=date(2026, 1, 5), category=ExpenseCategory.DRIVER, amount=Decimal("300"), driver=self.driver)
        r = self.client.post(f"/drivers/{self.driver.pk}/delete/", follow=True)
        from drivers.models import Driver
        self.assertTrue(Driver.objects.filter(pk=self.driver.pk).exists())
        self.assertEqual(Expense.objects.get().driver, self.driver)  # pay history keeps its driver
        self.assertIn("Inactive", " ".join(str(m) for m in r.context["messages"]))

    def test_database_level_protection_covers_bulk_deletes_and_the_admin(self):
        from django.db.models import ProtectedError
        from customers.models import Customer
        Shipment.objects.create(customer=self.customer, date=date(2026, 1, 5), booking="B", container="C", seal="S",
                                amount=Decimal("1"), status=ShipmentStatus.CONFIRMED)
        Invoice.objects.create(customer=self.customer, invoice_date=date(2026, 1, 5))
        with self.assertRaises(ProtectedError):
            Customer.objects.filter(pk=self.customer.pk).delete()
        self.assertEqual((Shipment.objects.count(), Invoice.objects.count()), (1, 1))

    def test_vehicle_status_badges_use_the_stored_codes(self):
        """The templates compared with 'active'/'maintenance', so every truck was labelled 'Retired'."""
        Vehicle.objects.filter(pk=self.vehicle.pk).update(status="IU")
        self.assertContains(self.client.get("/vehicles/"), "In Use")
        self.assertContains(self.client.get(f"/vehicles/{self.vehicle.pk}/"), "In Use")
        Vehicle.objects.filter(pk=self.vehicle.pk).update(status="RE")
        self.assertContains(self.client.get("/vehicles/"), "Retired")


class VehicleFixesQA040(QABase):
    def setUp(self):
        super().setUp()
        self.login()

    def maintenance_post(self, **over):
        data = {"vehicle": self.vehicle.pk, "cost": "120.00", "service_provider": "Joe's Garage", "type": "Oil change",
                "description": "Done", "date": "2025-03-14", "mileage_at_service": "100000", "next_service_mileage": "110000"}
        data.update(over)
        return self.client.post("/vehicles/maintenance/create/", data)

    def test_a_past_service_keeps_the_date_that_was_entered(self):
        self.assertEqual(self.maintenance_post().status_code, 302)
        self.assertEqual(str(Maintenance.objects.get().date), "2025-03-14")

    def test_date_defaults_to_today_in_business_time_when_left_empty_in_code(self):
        from django.utils import timezone
        m = Maintenance.objects.create(vehicle=self.vehicle, cost=1, service_provider="x", description="d")
        self.assertEqual(m.date, timezone.localdate())

    def test_form_shows_the_date_field_and_editing_keeps_the_stored_date(self):
        self.maintenance_post()
        m = Maintenance.objects.get()
        self.assertContains(self.client.get(f"/vehicles/maintenance/{m.pk}/edit/"), 'name="date"')
        self.client.post(f"/vehicles/maintenance/{m.pk}/edit/", dict(vehicle=self.vehicle.pk, cost="130", service_provider="x",
                         type="Oil", description="d", date="2025-03-14", mileage_at_service="1"))
        m.refresh_from_db()
        self.assertEqual((str(m.date), m.cost), ("2025-03-14", Decimal("130.00")))

    def test_sync_without_a_gomotive_id_explains_what_to_do(self):
        r = self.client.post(f"/vehicles/{self.vehicle.pk}/sync/", follow=True)
        self.assertEqual(r.status_code, 200)
        text = " ".join(str(m) for m in r.context["messages"])
        self.assertIn(self.vehicle.registration_number, text)
        self.assertIn("no GoMotive ID", text)

    def test_maintenance_delete_confirmation_names_the_truck(self):
        m = Maintenance.objects.create(vehicle=self.vehicle, cost=1, service_provider="x", description="d")
        self.assertContains(self.client.get(f"/vehicles/maintenance/{m.pk}/delete/"), self.vehicle.registration_number)
