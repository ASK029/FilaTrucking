"""Shared fixtures for the QA suite. Fake data only."""
import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import Client, TestCase

from customers.models import Customer
from drivers.models import Driver
from vehicles.models import Vehicle

# The ingest secret is a hard-coded constant in shipments/api.py (itself a finding); import it, never print it.
from shipments.api import WHATSAPP_SECRET


class QABase(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user("qa_admin", "qa@example.com", "qa-pass-123")
        cls.customer = Customer.objects.create(
            name="Acme Shipping Line", abbreviation="ASL", phone_number="+13125550100",
            address="1 Main St", email="ap@acme.example", default_rate=Decimal("400.00"),
        )
        cls.driver = Driver.objects.create(name="Moe Driver", phone_number="+13125550101", license_number="LIC-001")
        cls.vehicle = Vehicle.objects.create(registration_number="T-101", model="579", chassis_number="1XKYD49X0LJ000001")

    def setUp(self):
        # a 500 must surface as a response (so we can assert on it) instead of raising inside the test
        self.client = Client(raise_request_exception=False)

    def login(self):
        self.client.force_login(self.user)

    def ingest(self, text=None, sender="13125550101@s.whatsapp.net", token=WHATSAPP_SECRET, raw=None, **extra):
        body = raw if raw is not None else json.dumps({"text": text, "sender": sender, **extra})
        headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
        return self.client.post("/shipments/api/ingest/", data=body, content_type="application/json", **headers)


def formset_data(prefix, rows, initial=0):
    data = {
        f"{prefix}-TOTAL_FORMS": len(rows), f"{prefix}-INITIAL_FORMS": initial,
        f"{prefix}-MIN_NUM_FORMS": 0, f"{prefix}-MAX_NUM_FORMS": 1000,
    }
    for i, row in enumerate(rows):
        for k, v in row.items():
            data[f"{prefix}-{i}-{k}"] = v
    return data
