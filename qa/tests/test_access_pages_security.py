"""Whole-site sweeps: authentication on every route, no 500s on every page, secret exposure, brute force."""
import re
import tempfile
from datetime import date
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import URLPattern, URLResolver, get_resolver

from drivers.models import DriverDocument
from shipments.models import Expense, Invoice, InvoiceLineItem, Shipment, SystemSettings, WhatsAppGroup
from vehicles.models import Maintenance

from .base import QABase

PARAM = re.compile(r"<(?:\w+:)?(\w+)>")


def walk(patterns, prefix=""):
    for p in patterns:
        if isinstance(p, URLResolver):
            yield from walk(p.url_patterns, prefix + str(p.pattern))
        elif isinstance(p, URLPattern):
            yield prefix + str(p.pattern), p.name


class SiteSweep(QABase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        cls.shipment = Shipment.objects.create(customer=cls.customer, date=date(2026, 1, 5), booking="B", container="C",
                                               seal="S", location="CHI", amount=Decimal("400"))
        cls.invoice = Invoice.objects.create(customer=cls.customer, invoice_date=date(2026, 1, 10))
        InvoiceLineItem.objects.create(invoice=cls.invoice, shipment=cls.shipment, date_incurred=date(2026, 1, 5),
                                       description="d", location="CHI", amount=Decimal("400"))
        cls.expense = Expense.objects.create(date=date(2026, 1, 5), amount=Decimal("10"))
        cls.maint = Maintenance.objects.create(vehicle=cls.vehicle, cost=1, service_provider="x", description="d")
        cls.group = WhatsAppGroup.objects.create(group_jid="1-2@g.us", group_name="g")

    def urls(self):
        for pattern, name in walk(get_resolver().url_patterns):
            if pattern.startswith(("admin/", "auth/", "media/", "static/")) or "<path:" in pattern:
                continue

            def sub(m, name=name):
                key, n = m.group(1), name or ""
                if key == "invoice_pk" or n.startswith("invoice"):
                    return str(self.invoice.pk)
                if key == "group_id":
                    return str(self.group.pk)
                for pre, obj in (("customer", self.customer), ("driver", self.driver), ("shipment", self.shipment),
                                 ("expense", self.expense), ("vehicle", self.vehicle), ("sync_vehicle", self.vehicle),
                                 ("maintenance", self.maint)):
                    if n.startswith(pre):
                        return str(obj.pk)
                return "1"

            yield "/" + PARAM.sub(sub, pattern), name

    def test_every_route_requires_authentication(self):
        """[AUTH-SWEEP] anonymous GET must redirect to login / 401 / 405 for every route."""
        bad = []
        for path, name in self.urls():
            r = self.client.get(path)
            if r.status_code not in (301, 302, 401, 403, 405):
                bad.append(f"{r.status_code} {path} ({name})")
        self.assertEqual(bad, [], "routes answering an anonymous GET")

    def test_every_page_renders_without_server_error(self):
        self.login()
        bad = []
        for path, name in self.urls():
            if path.endswith(("/email/", "/pdf/")):  # side effects / WeasyPrint (covered in the container run)
                continue
            r = self.client.get(path)
            if r.status_code >= 500:
                bad.append(f"{r.status_code} {path} ({name})")
        self.assertEqual(bad, [])

    def test_404_for_unknown_objects(self):
        self.login()
        for path in ("/customers/999999/", "/drivers/999999/", "/shipments/999999/", "/invoices/999999/", "/vehicles/999999/"):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)


class SecretsAndUploads(QABase):
    def test_system_settings_page_does_not_echo_stored_secrets(self):
        """[SEC-ECHO] PasswordInput(render_value=True) writes the Motive API key and SMTP password into the HTML."""
        s = SystemSettings.get_instance()
        s.set_motive_api_key("MOTIVE-KEY-FOR-QA-12345")
        s.set_email_password("SMTP-PASSWORD-FOR-QA-67890")
        s.save()
        self.login()
        html = self.client.get("/settings/system/").content.decode()
        leaked = [w for w in ("MOTIVE-KEY-FOR-QA-12345", "SMTP-PASSWORD-FOR-QA-67890") if w in html]
        self.assertEqual(len(leaked), 0, f"{len(leaked)} secret(s) rendered in page source")

    def test_uploaded_documents_get_unguessable_names(self):
        """[SEC-MEDIA] nginx serves /media/ with no auth; driver licences keep their original, guessable file name."""
        with tempfile.TemporaryDirectory() as tmp, override_settings(MEDIA_ROOT=tmp):
            d = DriverDocument.objects.create(driver=self.driver, name="CDL",
                                              document=SimpleUploadedFile("license.pdf", b"%PDF-1.4"))
            self.assertNotEqual(d.document.name, "driver_documents/license.pdf")

    def test_login_is_rate_limited(self):
        """[SEC-BRUTEFORCE] 15 wrong passwords in a row are all processed normally."""
        codes = [self.client.post("/auth/login/", {"username": "qa_admin", "password": f"wrong{i}"}).status_code
                 for i in range(15)]
        self.assertTrue(any(c in (403, 429) for c in codes), "no lockout / throttling after repeated failures")
