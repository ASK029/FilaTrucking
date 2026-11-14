"""N+1 query detection: query count must not grow with the number of rows."""
from datetime import date
from decimal import Decimal

from django.db import connection
from django.test.utils import CaptureQueriesContext

from shipments.models import Expense, Invoice, Shipment
from vehicles.models import Maintenance, Vehicle

from .base import QABase


class QueryCounts(QABase):
    def count(self, url):
        with CaptureQueriesContext(connection) as c:
            r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        return len(c)

    def grow(self, make, url, small=3, large=30):
        self.login()
        for i in range(small):
            make(i)
        a = self.count(url)
        for i in range(small, large):
            make(i)
        return a, self.count(url)

    def assert_flat(self, counts, what):
        a, b = counts
        self.assertLessEqual(b, a + 4, f"{what}: {a} queries for 3 rows -> {b} queries for 30 rows (N+1)")

    def test_shipment_list(self):
        def mk(i):
            Shipment.objects.create(customer=self.customer, driver=self.driver, vehicle=self.vehicle, date=date(2026, 1, 5),
                                    booking=f"B{i}", container=f"C{i}", seal="S", location="CHI", amount=Decimal("1"))
        self.assert_flat(self.grow(mk, "/shipments/"), "shipment list")

    def test_invoice_list(self):
        def mk(i):
            Invoice.objects.create(customer=self.customer, invoice_date=date(2026, 1, 5))
        self.assert_flat(self.grow(mk, "/invoices/"), "invoice list")

    def test_expense_list(self):
        def mk(i):
            Expense.objects.create(date=date(2026, 1, 5), amount=Decimal("1"), vehicle=self.vehicle, driver=self.driver)
        self.assert_flat(self.grow(mk, "/expenses/"), "expense list")

    def test_maintenance_list(self):
        def mk(i):
            v = Vehicle.objects.create(registration_number=f"T{i}", model="m", chassis_number=f"V{i}")
            Maintenance.objects.create(vehicle=v, cost=1, service_provider="x", description="d")
        self.assert_flat(self.grow(mk, "/vehicles/maintenance/"), "maintenance list")

    def test_vehicle_list(self):
        def mk(i):
            Vehicle.objects.create(registration_number=f"V{i}", model="m", chassis_number=f"VIN{i}", driver=self.driver)
        self.assert_flat(self.grow(mk, "/vehicles/"), "vehicle list")
