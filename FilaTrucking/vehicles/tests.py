from django.test import TestCase

from .gomotive_client import GoMotiveClient
from .models import Maintenance, Vehicle


class GoMotiveClientTests(TestCase):
    def test_client_initialization_without_env(self) -> None:
        client = GoMotiveClient(base_url="https://api.example.com", api_key="test-key")
        self.assertEqual(client.base_url, "https://api.example.com")
        self.assertEqual(client.api_key, "test-key")


class MaintenanceAlertLogicTests(TestCase):
    def test_next_service_mileage_field_exists(self) -> None:
        vehicle = Vehicle.objects.create(
            driver_id=None,  # type: ignore[arg-type]
            registration_number="TEST123",
            name="Test Truck",
            Manufacturer="Make",
            model="Model",
            year=2024,
            chassis_number="CHASSIS",
            engine_number="ENGINE",
            ownership_type="CO",
            image="vehicle_images/test.jpg",
            current_odometer=9500,
        )
        m = Maintenance.objects.create(
            vehicle=vehicle,
            cost=0,
            service_provider="Test",
            type="Oil Change",
            description="Test",
            mileage_at_service=9000,
            next_service_mileage=10000,
        )
        self.assertEqual(m.next_service_mileage, 10000)
