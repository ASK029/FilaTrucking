"""QA-052: the company information on invoices is editable in System Settings."""
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core import mail

from shipments.models import Invoice, InvoiceLineItem, SystemSettings

from .base import QABase


class CapturingHTML:
    """Stands in for WeasyPrint and remembers the HTML it was given."""
    last_html = ""

    def __init__(self, string=None, **kwargs):
        CapturingHTML.last_html = string

    def write_pdf(self):
        return b"%PDF-1.4 fake"


NEW = {
    "company_name": "Acme Hauling LLC",
    "company_address": "500 Harbor Road\nSuite 7\nGary, IN 46402",
    "company_phone": "(219) 555-0142",
    "company_email": "billing@acme-hauling.example",
    "email_host_user": "", "email_from_email": "",
}


class CompanySettings(QABase):
    def setUp(self):
        super().setUp()
        self.login()
        self.invoice = Invoice.objects.create(customer=self.customer, invoice_date=date(2026, 1, 10))
        InvoiceLineItem.objects.create(invoice=self.invoice, date_incurred=date(2026, 1, 5), description="",
                                       location="ASL", amount=Decimal("400"))

    def save(self, **over):
        return self.client.post("/settings/system/", {**NEW, **over})

    def test_defaults_are_the_text_that_used_to_be_hard_coded(self):
        s = SystemSettings.get_instance()
        self.assertEqual((s.company_name, s.company_phone, s.company_email), ("Fila Trucking", "(555) 012-3456", ""))
        self.assertEqual(s.company_address_lines, ["123 Fleet Street, Suite 100", "Chicago, IL 60601"])

    def test_the_settings_page_shows_and_saves_the_company_fields(self):
        r = self.client.get("/settings/system/")
        for name in ("company_name", "company_address", "company_phone", "company_email"):
            self.assertContains(r, f'name="{name}"')
        self.assertEqual(self.save().status_code, 302)
        s = SystemSettings.get_instance()
        self.assertEqual((s.company_name, s.company_phone, s.company_email), (NEW["company_name"], NEW["company_phone"], NEW["company_email"]))
        self.assertEqual(s.company_address_lines, ["500 Harbor Road", "Suite 7", "Gary, IN 46402"])

    def test_saving_the_company_does_not_wipe_stored_secrets(self):
        s = SystemSettings.get_instance()
        s.set_motive_api_key("fake-motive-key")
        s.save()
        self.save()
        self.assertEqual(SystemSettings.get_instance().get_motive_api_key(), "fake-motive-key")

    def test_name_is_required_and_the_address_is_limited(self):
        self.assertEqual(self.save(company_name="").status_code, 200)
        self.assertEqual(self.save(company_address="x" * 301).status_code, 200)
        self.assertEqual(SystemSettings.get_instance().company_name, "Fila Trucking")
        self.assertEqual(self.save(company_address="", company_phone="", company_email="").status_code, 302)

    def test_a_bad_company_email_is_refused(self):
        self.assertEqual(self.save(company_email="not-an-email").status_code, 200)

    def test_the_invoice_screen_uses_the_company_details(self):
        self.save()
        r = self.client.get(f"/invoices/{self.invoice.pk}/")
        for text in ("Acme Hauling LLC", "500 Harbor Road", "Gary, IN 46402", "(219) 555-0142", "billing@acme-hauling.example"):
            self.assertContains(r, text)
        self.assertNotContains(r, "123 Fleet Street")
        self.assertNotContains(r, "billing@filatrucking.com")

    def test_the_pdf_uses_the_company_details(self):
        self.save()
        with mock.patch("shipments.views.HTML", CapturingHTML):
            self.assertEqual(self.client.get(f"/invoices/{self.invoice.pk}/pdf/").status_code, 200)
        html = CapturingHTML.last_html
        for text in ("Acme Hauling LLC", "500 Harbor Road", "Suite 7", "Gary, IN 46402", "(219) 555-0142", "billing@acme-hauling.example"):
            self.assertIn(text, html)
        self.assertNotIn("123 Fleet Street", html)

    def test_empty_optional_fields_leave_no_blank_labels(self):
        self.save(company_address="", company_phone="", company_email="")
        with mock.patch("shipments.views.HTML", CapturingHTML):
            self.client.get(f"/invoices/{self.invoice.pk}/pdf/")
        self.assertNotIn("Phone:", CapturingHTML.last_html)
        r = self.client.get(f"/invoices/{self.invoice.pk}/")
        self.assertNotContains(r, "Phone:")
        self.assertNotContains(r, "Email:")

    def test_the_invoice_email_names_the_company(self):
        self.save()
        with mock.patch("shipments.views.HTML", CapturingHTML):
            self.client.post(f"/invoices/{self.invoice.pk}/email/")
        message = mail.outbox[0]
        self.assertEqual(message.subject, f"Invoice #{self.invoice.pk} from Acme Hauling LLC")
        self.assertTrue(message.body.rstrip().endswith("Acme Hauling LLC"))
        self.assertNotIn("Fila Trucking", message.subject + message.body)

    def test_company_text_is_escaped(self):
        self.save(company_name="<script>alert(1)</script>", company_address="A & B <b>Rd</b>")
        r = self.client.get(f"/invoices/{self.invoice.pk}/")
        self.assertNotContains(r, "<script>alert(1)</script>")
        self.assertNotContains(r, "<b>Rd</b>")
        with mock.patch("shipments.views.HTML", CapturingHTML):
            self.client.get(f"/invoices/{self.invoice.pk}/pdf/")
        self.assertNotIn("<script>alert(1)</script>", CapturingHTML.last_html)

    def test_settings_need_a_login(self):
        self.client.logout()
        r = self.client.post("/settings/system/", NEW)
        self.assertEqual(r.status_code, 302)
        self.assertIn("/auth/login/", r["Location"])
        self.assertEqual(SystemSettings.get_instance().company_name, "Fila Trucking")
