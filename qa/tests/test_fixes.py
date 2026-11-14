"""Regression tests for individual QA tasks that don't belong to a bigger area file."""
from datetime import date
from decimal import Decimal

from shipments.models import Shipment, ShipmentStatus

from .base import QABase


class ShipmentDetailPage(QABase):
    def make(self, **kw):
        return Shipment.objects.create(customer=self.customer, date=date(2026, 1, 5), booking="B1", container="C1",
                                       seal="S1", amount=Decimal("400"), status=ShipmentStatus.PENDING_REVIEW, **kw)

    def test_detail_renders_for_shipment_without_driver_and_vehicle(self):
        """[QA-007] every WhatsApp shipment has no driver/vehicle; the page used to 500 with NoReverseMatch."""
        self.login()
        r = self.client.get(f"/shipments/{self.make().pk}/")
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Unassigned", count=2)

    def test_detail_links_driver_and_vehicle_when_present(self):
        self.login()
        r = self.client.get(f"/shipments/{self.make(driver=self.driver, vehicle=self.vehicle).pk}/")
        self.assertContains(r, f"/drivers/{self.driver.pk}/")
        self.assertContains(r, f"/vehicles/{self.vehicle.pk}/")
        self.assertContains(r, self.vehicle.registration_number)
        self.assertNotContains(r, "Unassigned")
