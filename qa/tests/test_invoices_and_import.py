"""Invoice lifecycle, email, in-app CSV import."""
from datetime import date
from decimal import Decimal
from unittest import mock

from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile

from shipments.forms import InvoiceLineItemFormSet
from shipments.models import Expense, Invoice, InvoiceLineItem, InvoiceStatus, Shipment, ShipmentStatus

from .base import QABase, formset_data

PREFIX = InvoiceLineItemFormSet.get_default_prefix()


class FakeHTML:
    def __init__(self, *a, **k):
        pass

    def write_pdf(self):
        return b"%PDF-1.4 fake"


def line(shipment, **over):
    row = {"shipment": shipment.pk, "date_incurred": str(shipment.date), "description": f"Shipment {shipment.container}",
           "booking_no": shipment.booking, "container_no": shipment.container, "seal_no": shipment.seal,
           "location": "CHI", "amount": "400.00"}
    row.update(over)
    return row


class InvoiceBase(QABase):
    def setUp(self):
        super().setUp()
        self.login()
        self.s1 = self.shipment("C1", "B1")
        self.s2 = self.shipment("C2", "B2")

    def shipment(self, container, booking, status=ShipmentStatus.CONFIRMED):
        return Shipment.objects.create(customer=self.customer, date=date(2026, 1, 5), booking=booking, container=container,
                                       seal="S", location="CHI", amount=Decimal("400"), status=status)

    def invoice_with(self, shipment, status=InvoiceStatus.DRAFT, paid_at=None):
        inv = Invoice.objects.create(customer=self.customer, invoice_date=date(2026, 1, 10), status=status, paid_at=paid_at)
        InvoiceLineItem.objects.create(invoice=inv, shipment=shipment, date_incurred=shipment.date, description="d",
                                       location="CHI", amount=Decimal("400"))
        inv.calculate_total()
        return inv


class InvoiceCreate(InvoiceBase):
    def post_create(self, rows):
        data = {"customer": self.customer.pk, "invoice_date": "2026-01-10", "status": "draft"}
        data.update(formset_data(PREFIX, rows))
        return self.client.post("/invoices/create/", data)

    def test_create_marks_shipments_invoiced_and_totals(self):
        r = self.post_create([line(self.s1), line(self.s2)])
        self.assertEqual(r.status_code, 302)
        inv = Invoice.objects.get()
        self.assertEqual(inv.total_amount, Decimal("800.00"))
        self.s1.refresh_from_db()
        self.assertEqual(self.s1.status, ShipmentStatus.INVOICED)

    def test_invalid_line_item_does_not_leave_an_orphan_invoice(self):
        """[INV-ORPHAN] invoice.save() runs before the formset is validated, with no transaction."""
        self.post_create([line(self.s1, amount="")])
        self.assertEqual(Invoice.objects.count(), 0)

    def test_invalid_submission_keeps_what_the_user_typed(self):
        """[INV-LOSE-INPUT] on error the view replaces the bound formset with an empty one."""
        r = self.post_create([line(self.s1, amount="")])
        self.assertContains(r, "B1")

    def test_shipment_cannot_be_invoiced_twice(self):
        """[INV-DOUBLE] nothing stops the same shipment appearing on two invoices."""
        self.invoice_with(self.s1)
        other = Invoice.objects.create(customer=self.customer, invoice_date=date(2026, 1, 12))
        self.client.post(f"/invoices/{other.pk}/lines/", formset_data(PREFIX, [line(self.s1)]))
        self.assertEqual(InvoiceLineItem.objects.filter(shipment=self.s1).count(), 1)


class InvoiceLifecycle(InvoiceBase):
    def test_removing_a_line_returns_the_shipment_to_confirmed(self):
        """[INV-RESTORE] only invoice_delete restores status; removing one line leaves the shipment 'invoiced' forever."""
        inv = self.invoice_with(self.s1)
        self.s1.status = ShipmentStatus.INVOICED
        self.s1.save()
        li = inv.line_items.get()
        data = formset_data(PREFIX, [dict(line(self.s1), id=li.pk, invoice=inv.pk, DELETE="on")], initial=1)
        self.client.post(f"/invoices/{inv.pk}/lines/", data)
        self.s1.refresh_from_db()
        self.assertEqual(self.s1.status, ShipmentStatus.CONFIRMED)

    def test_delete_draft_restores_shipments(self):
        inv = self.invoice_with(self.s1)
        self.client.post(f"/invoices/{inv.pk}/delete/")
        self.s1.refresh_from_db()
        self.assertEqual((Invoice.objects.count(), self.s1.status), (0, ShipmentStatus.CONFIRMED))

    def test_delete_sent_invoice_is_blocked(self):
        inv = self.invoice_with(self.s1, status=InvoiceStatus.SENT)
        self.client.post(f"/invoices/{inv.pk}/delete/")
        self.assertEqual(Invoice.objects.count(), 1)

    def test_change_status_to_paid_sets_paid_at(self):
        inv = self.invoice_with(self.s1)
        self.client.post(f"/invoices/{inv.pk}/change-status/", {"status": "paid"})
        inv.refresh_from_db()
        self.assertEqual((inv.status, inv.paid_at), ("paid", date.today()))

    def test_change_status_rejects_garbage(self):
        inv = self.invoice_with(self.s1)
        self.client.post(f"/invoices/{inv.pk}/change-status/", {"status": "hacked"})
        inv.refresh_from_db()
        self.assertEqual(inv.status, "draft")


class InvoiceEmail(InvoiceBase):
    def test_sending_email_requires_post(self):
        """[INV-GET-EMAIL] a plain GET (link, prefetch, crawler) emails the customer and changes status."""
        inv = self.invoice_with(self.s1)
        with mock.patch("shipments.views.HTML", FakeHTML):
            self.client.get(f"/invoices/{inv.pk}/email/")
        self.assertEqual(len(mail.outbox), 0)

    def test_email_does_not_downgrade_a_paid_invoice(self):
        """[INV-PAID-RESET] re-sending a PAID invoice flips it to 'sent' and wipes paid_at, removing it from revenue."""
        inv = self.invoice_with(self.s1, status=InvoiceStatus.PAID, paid_at=date(2026, 1, 20))
        with mock.patch("shipments.views.HTML", FakeHTML):
            self.client.post(f"/invoices/{inv.pk}/email/")
        inv.refresh_from_db()
        self.assertEqual((inv.status, inv.paid_at), ("paid", date(2026, 1, 20)))

    def test_email_attaches_pdf_and_marks_sent(self):
        inv = self.invoice_with(self.s1)
        with mock.patch("shipments.views.HTML", FakeHTML):
            self.client.post(f"/invoices/{inv.pk}/email/")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].attachments[0][2], "application/pdf")
        inv.refresh_from_db()
        self.assertEqual(inv.status, "sent")

    def test_customer_without_email_is_handled(self):
        self.customer.email = ""
        self.customer.save()
        inv = self.invoice_with(self.s1)
        r = self.client.post(f"/invoices/{inv.pk}/email/")
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(mail.outbox), 0)


class CsvImport(QABase):
    def test_valid_csv_imports_rows(self):
        """[CSV-EMPTY] `rows = list(reader)` exhausts the DictReader, so the loop below it never runs."""
        self.login()
        csv_bytes = b"DATE,Category,Description,Amount\n01-05-2026,FUEL,Pilot,$250.00\n01-06-2026,TOLL,IPASS,12.50\n"
        r = self.client.post("/settings/import/process/", {"csv_file": SimpleUploadedFile("jan.csv", csv_bytes)}, follow=True)
        msgs = [str(m) for m in r.context["messages"]]
        self.assertEqual(Expense.objects.count(), 2, f"messages shown to user: {msgs}")

    def test_import_stores_category_amount_date_and_note(self):
        self.login()
        csv_bytes = (b"DATE,Category,Description,Amount\n"
                     b'01-05-2026,FUEL,Pilot,"$1,250.50"\n'
                     b"01/06/2026,toll,IPASS,12.50\n"
                     b",FUEL,no date,5\n"
                     b"01-07-2026,FUEL,zero,0\n")
        self.client.post("/settings/import/process/", {"csv_file": SimpleUploadedFile("jan.csv", csv_bytes)})
        got = sorted(Expense.objects.values_list("date", "category", "amount", "notes"))
        self.assertEqual(got, [(date(2026, 1, 5), "fuel", Decimal("1250.50"), "Imported: Pilot"),
                               (date(2026, 1, 6), "toll", Decimal("12.50"), "Imported: IPASS")])

    def test_import_is_all_or_nothing(self):
        self.login()
        csv_bytes = b"DATE,Category,Description,Amount\n01-05-2026,FUEL,a,10\n01-06-2026,FUEL,b,20\n"
        real_create = Expense.objects.create
        calls = []

        def flaky(**kw):
            calls.append(kw)
            if len(calls) == 2:
                raise RuntimeError("disk full")
            return real_create(**kw)

        with mock.patch.object(Expense.objects, "create", side_effect=flaky):
            self.client.post("/settings/import/process/", {"csv_file": SimpleUploadedFile("jan.csv", csv_bytes)})
        self.assertEqual(Expense.objects.count(), 0)

    def test_non_csv_is_rejected(self):
        self.login()
        self.client.post("/settings/import/process/", {"csv_file": SimpleUploadedFile("x.exe", b"MZ")}, follow=True)
        self.assertEqual(Expense.objects.count(), 0)


class InvoiceLinesXss(InvoiceBase):
    """[QA-005] shipment text reaches the page inside a <script>; it must not be able to break out."""

    PAYLOAD = '</script><script>window.__xss=1</script>'

    def test_script_breakout_in_shipment_fields_is_inert(self):
        for field in ("location", "booking", "container", "seal"):
            with self.subTest(field=field):
                Shipment.objects.filter(pk=self.s1.pk).update(**{field: self.PAYLOAD})
                inv = Invoice.objects.create(customer=self.customer, invoice_date=date(2026, 1, 10))
                html = self.client.get(f"/invoices/{inv.pk}/lines/").content.decode()
                self.assertNotIn("</script><script>window.__xss", html)
                self.assertIn('id="shipment-data"', html)

    def test_shipment_data_is_still_available_to_the_page_script(self):
        import json, re
        inv = Invoice.objects.create(customer=self.customer, invoice_date=date(2026, 1, 10))
        html = self.client.get(f"/invoices/{inv.pk}/lines/").content.decode()
        raw = re.search(r'<script id="shipment-data" type="application/json">(.*?)</script>', html, re.S).group(1)
        data = json.loads(raw)
        self.assertEqual(data[str(self.s1.pk)]["container_no"], "C1")
