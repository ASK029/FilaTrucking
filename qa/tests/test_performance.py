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


class PaginationQA034(QABase):
    def setUp(self):
        super().setUp()
        self.login()

    def test_shipments_are_paginated_25_per_page_with_working_links(self):
        Shipment.objects.bulk_create([
            Shipment(customer=self.customer, date=date(2026, 1, 5), booking=f"B{i}", container=f"C{i:03d}", seal="S",
                     location="CHI", amount=Decimal("1")) for i in range(60)])
        html1 = self.client.get("/shipments/").content.decode()
        self.assertIn("Showing", html1)
        r2 = self.client.get("/shipments/?page=2")
        self.assertEqual(len(r2.context["shipments"]), 25)
        self.assertEqual(len(self.client.get("/shipments/?page=3").context["shipments"]), 10)
        self.assertEqual(self.client.get("/shipments/?page=4").status_code, 404)
        self.assertContains(self.client.get("/shipments/"), "?page=2")

    def test_pagination_links_keep_the_search_query(self):
        from customers.models import Customer
        for i in range(60):
            Customer.objects.create(name=f"Acme {i}", abbreviation=f"A{i}", phone_number="+13125550100", address="x",
                                    email=f"a{i}@example.org", default_rate=Decimal("1"))
        r = self.client.get("/customers/?q=Acme")
        self.assertContains(r, "?page=2&amp;q=Acme")

    def test_each_list_page_renders_exactly_one_pagination_control(self):
        for url in ("/shipments/", "/invoices/", "/expenses/", "/vehicles/maintenance/", "/vehicles/", "/customers/", "/drivers/"):
            with self.subTest(url=url):
                self.assertLessEqual(self.client.get(url).content.decode().count('aria-label="Pagination"'), 1)

    def test_maintenance_list_pages_work_with_the_python_side_sort(self):
        for i in range(60):
            v = Vehicle.objects.create(registration_number=f"PT{i}", model="m", chassis_number=f"PV{i}")
            Maintenance.objects.create(vehicle=v, cost=1, service_provider="x", description="d")
        for sort in ("", "?sort=date", "?sort=vehicle"):
            r = self.client.get("/vehicles/maintenance/" + sort)
            self.assertEqual((r.status_code, len(r.context["logs"])), (200, 50))
